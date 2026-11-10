"""TSheets (QuickBooks Time) integration for Home Assistant."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

from .api import TSheetsApi
from .const import (
    ACTION_IN,
    ACTION_OUT,
    ACTION_TOGGLE,
    CARDS_URL,
    CARDS_VERSION,
    DOMAIN,
    KEYS,
    SERVICE_CLOCK_IN,
    SERVICE_CLOCK_OUT,
    SERVICE_KEYPAD,
    SERVICE_PUNCH,
)
from .coordinator import TSheetsCoordinator
from .data import TSheetsConfigEntry, TSheetsRuntime
from .photos import PhotoStore
from .punch import PunchClock, PunchError

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.DEVICE_TRACKER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PUNCH_SCHEMA = vol.Schema(
    {
        vol.Required("pin"): vol.All(cv.string, vol.Match(r"^\d{4}$")),
        vol.Optional("action", default=ACTION_TOGGLE): vol.In(
            [ACTION_TOGGLE, ACTION_IN, ACTION_OUT]
        ),
        vol.Optional("jobcode_id"): cv.positive_int,
    }
)
KEYPAD_SCHEMA = vol.Schema({vol.Required("key"): vol.In(KEYS)})
CLOCK_IN_SCHEMA = vol.Schema(
    {vol.Required("user_id"): cv.positive_int, vol.Optional("jobcode_id"): cv.positive_int}
)
CLOCK_OUT_SCHEMA = vol.Schema({vol.Required("user_id"): cv.positive_int})


def _runtime(hass: HomeAssistant) -> TSheetsRuntime:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise HomeAssistantError("TSheets is not set up or not loaded")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and the dashboard cards (once, independent of config entries)."""
    await _async_register_cards(hass)

    async def _punch(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        try:
            result = await rt.punch_clock.async_punch(
                call.data["pin"], call.data["action"], call.data.get("jobcode_id")
            )
        except PunchError as err:
            raise ServiceValidationError(str(err)) from err
        return result if call.return_response else None

    async def _keypad(call: ServiceCall) -> None:
        await _runtime(hass).punch_clock.async_press(call.data["key"])

    async def _clock_in(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        try:
            result = await rt.punch_clock.async_punch_user(
                call.data["user_id"], ACTION_IN, call.data.get("jobcode_id")
            )
        except PunchError as err:
            raise ServiceValidationError(str(err)) from err
        return result if call.return_response else None

    async def _clock_out(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        try:
            result = await rt.punch_clock.async_punch_user(call.data["user_id"], ACTION_OUT)
        except PunchError as err:
            raise ServiceValidationError(str(err)) from err
        return result if call.return_response else None

    hass.services.async_register(
        DOMAIN, SERVICE_PUNCH, _punch, PUNCH_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, SERVICE_KEYPAD, _keypad, KEYPAD_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CLOCK_IN, _clock_in, CLOCK_IN_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLOCK_OUT, _clock_out, CLOCK_OUT_SCHEMA, SupportsResponse.OPTIONAL
    )
    return True


async def _async_register_cards(hass: HomeAssistant) -> None:
    """Serve frontend/tsheets-cards.js and load it on every dashboard (no Resources step)."""
    path = Path(__file__).parent / "frontend" / "tsheets-cards.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARDS_URL, str(path), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARDS_URL}?v={CARDS_VERSION}")


async def async_setup_entry(hass: HomeAssistant, entry: TSheetsConfigEntry) -> bool:
    api = TSheetsApi(async_get_clientsession(hass), entry.data[CONF_ACCESS_TOKEN])
    coordinator = TSheetsCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    photos = PhotoStore(hass)
    await photos.async_setup()
    await photos.async_scan(coordinator.data.users)

    punch_clock = PunchClock(hass, entry, coordinator, photos)
    entry.runtime_data = TSheetsRuntime(
        api=api, coordinator=coordinator, punch_clock=punch_clock, photos=photos
    )
    entry.async_on_unload(punch_clock.async_shutdown)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: TSheetsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TSheetsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
