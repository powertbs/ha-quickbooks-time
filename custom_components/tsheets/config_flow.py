"""Config + options flow: API token, settings and employee PINs."""
from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.helpers.selector import (
    BooleanSelector,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TSheetsApi, TSheetsAuthError, TSheetsError
from .const import (
    CONF_DEFAULT_JOBCODE,
    CONF_GENDERS,
    CONF_GPS_MAX_AGE,
    CONF_PINS,
    CONF_SCAN_INTERVAL,
    DEFAULT_GPS_MAX_AGE,
    DEFAULT_JOB_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PIN_LENGTH,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)
PIN_RE = re.compile(rf"^\d{{{PIN_LENGTH}}}$")


class TSheetsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, token: str) -> dict:
        api = TSheetsApi(async_get_clientsession(self.hass), token)
        return await api.get_current_user()

    async def _try_token(self, user_input: dict) -> tuple[str, dict | None, dict[str, str]]:
        token = user_input[CONF_ACCESS_TOKEN].strip()
        errors: dict[str, str] = {}
        user = None
        try:
            user = await self._validate(token)
        except TSheetsAuthError:
            errors["base"] = "invalid_auth"
        except TSheetsError:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating TSheets token")
            errors["base"] = "unknown"
        return token, user, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token, user, errors = await self._try_token(user_input)
            if user is not None:
                company = str(user.get("client_url") or user.get("id"))
                await self.async_set_unique_id(company)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"QuickBooks Time ({company})",
                    data={CONF_ACCESS_TOKEN: token},
                    options={CONF_PINS: {}},
                )
        return self.async_show_form(step_id="user", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token, user, errors = await self._try_token(user_input)
            if user is not None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_ACCESS_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TSheetsOptionsFlow()


class TSheetsOptionsFlow(OptionsFlow):
    """Menu: general settings, or assign employee PINs."""

    def _data(self):
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return None
        return self.config_entry.runtime_data.coordinator.data

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._data() is None:
            # Not connected (bad token etc.): only the API connection can be changed.
            return self.async_show_menu(step_id="init", menu_options=["api"])
        return self.async_show_menu(
            step_id="init", menu_options=["api", "pins", "photos", "genders", "settings"]
        )

    async def async_step_api(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Replace the QuickBooks Time API access token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_ACCESS_TOKEN].strip()
            try:
                user = await TSheetsApi(async_get_clientsession(self.hass), token).get_current_user()
            except TSheetsAuthError:
                errors["base"] = "invalid_auth"
            except TSheetsError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating TSheets token")
                errors["base"] = "unknown"
            else:
                company = str(user.get("client_url") or user.get("id"))
                if self.config_entry.unique_id and company != self.config_entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    # Saving the new token reloads the integration (update listener).
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data={**self.config_entry.data, CONF_ACCESS_TOKEN: token}
                    )
                    return self.async_create_entry(data=dict(self.config_entry.options))
        current = str(self.config_entry.data.get(CONF_ACCESS_TOKEN, ""))
        return self.async_show_form(
            step_id="api",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={
                "account": self.config_entry.title,
                "token_end": f"…{current[-4:]}" if current else "none",
            },
        )

    async def async_step_photos(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload (or remove) an employee's kiosk photo."""
        data = self._data()
        photos = self.config_entry.runtime_data.photos
        errors: dict[str, str] = {}
        if user_input is not None:
            uid = int(user_input["employee"])
            emp = data.users.get(uid)
            try:
                if user_input.get("remove"):
                    await photos.async_remove(uid, emp)
                elif file_id := user_input.get("photo"):

                    def _save() -> None:
                        with process_uploaded_file(self.hass, file_id) as path:
                            photos.save_sync(uid, emp, path)

                    await self.hass.async_add_executor_job(_save)
                else:
                    errors["base"] = "no_photo"
            except ValueError:
                errors["base"] = "bad_image"
            if not errors:
                await photos.async_scan(data.users)
                return self.async_create_entry(data=dict(self.config_entry.options))

        employees = sorted(data.users.values(), key=lambda e: e.name.lower())
        schema = vol.Schema(
            {
                vol.Required("employee"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=str(e.id),
                                label=e.name
                                + (" ✓ has photo" if photos.url(e.id, None) else ""),
                            )
                            for e in employees
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("photo"): FileSelector(
                    FileSelectorConfig(accept="image/jpeg,image/png,image/webp")
                ),
                vol.Optional("remove", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="photos", data_schema=schema, errors=errors)

    async def async_step_genders(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Male / female per employee - picks the placeholder avatar when there's no photo."""
        data = self._data()
        current: dict[str, str] = dict(self.config_entry.options.get(CONF_GENDERS, {}))
        employees = sorted(data.users.values(), key=lambda e: e.name.lower())
        labels = {f"{e.name} (ID {e.id})": e.id for e in employees}
        if user_input is not None:
            genders = {
                str(uid): user_input[label]
                for label, uid in labels.items()
                if user_input.get(label) in ("male", "female")
            }
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_GENDERS: genders}
            )
        choices = [
            SelectOptionDict(value="unset", label="Not set"),
            SelectOptionDict(value="male", label="Male"),
            SelectOptionDict(value="female", label="Female"),
        ]
        schema = vol.Schema(
            {
                vol.Required(label, default=current.get(str(uid), "unset")): SelectSelector(
                    SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
                )
                for label, uid in labels.items()
            }
        )
        return self.async_show_form(step_id="genders", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **opts,
                    CONF_DEFAULT_JOBCODE: int(user_input[CONF_DEFAULT_JOBCODE]),
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_GPS_MAX_AGE: int(user_input[CONF_GPS_MAX_AGE]),
                }
            )

        jobs = [
            SelectOptionDict(value="0", label=f'Automatic — customer named "{DEFAULT_JOB_NAME}" if there is one, otherwise no job')
        ] + [
            SelectOptionDict(value=str(jid), label=label)
            for jid, label in self._data().clockable_jobcodes()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEFAULT_JOBCODE, default=str(opts.get(CONF_DEFAULT_JOBCODE, 0))
                ): SelectSelector(
                    SelectSelectorConfig(options=jobs, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=30, max=900, step=10, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_GPS_MAX_AGE, default=opts.get(CONF_GPS_MAX_AGE, DEFAULT_GPS_MAX_AGE)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=1440, step=5, unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_pins(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        data = self._data()
        current: dict[str, str] = dict(self.config_entry.options.get(CONF_PINS, {}))
        employees = sorted(data.users.values(), key=lambda e: e.name.lower())
        # Form field label -> user id. The label is what the admin sees.
        labels = {f"{e.name} (ID {e.id})": e.id for e in employees}

        errors: dict[str, str] = {}
        placeholders = {"names": ""}
        if user_input is not None:
            pins: dict[str, str] = {}
            bad, seen, dupes = [], {}, []
            for label, uid in labels.items():
                pin = str(user_input.get(label) or "").strip()
                if not pin:
                    continue
                if not PIN_RE.match(pin):
                    bad.append(label)
                    continue
                if pin in seen:
                    dupes.append(f"{seen[pin]} / {label}")
                seen.setdefault(pin, label)
                pins[str(uid)] = pin
            if bad:
                errors["base"] = "pin_invalid"
                placeholders["names"] = ", ".join(bad)
            elif dupes:
                errors["base"] = "pin_duplicate"
                placeholders["names"] = ", ".join(dupes)
            else:
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_PINS: pins}
                )
            current = {str(labels[k]): str(v or "") for k, v in user_input.items() if k in labels}

        schema = vol.Schema(
            {
                vol.Optional(
                    label, description={"suggested_value": current.get(str(uid), "")}
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
                for label, uid in labels.items()
            }
        )
        return self.async_show_form(
            step_id="pins",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )
