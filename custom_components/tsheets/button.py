"""Clock In / Clock Out buttons for the kiosk employee screen.

The button that doesn't apply is 'unavailable', which dashboards draw grayed out.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTION_IN, ACTION_OUT, MODE_EMPLOYEE
from .data import TSheetsConfigEntry
from .entity import hub_device
from .punch import PunchClock


async def async_setup_entry(
    hass: HomeAssistant, entry: TSheetsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    pc = entry.runtime_data.punch_clock
    async_add_entities([KioskButton(pc, entry, ACTION_IN), KioskButton(pc, entry, ACTION_OUT)])


class KioskButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, punch_clock: PunchClock, entry: TSheetsConfigEntry, action: str) -> None:
        self.punch_clock = punch_clock
        self.action = action
        self._attr_name = "Clock in" if action == ACTION_IN else "Clock out"
        self._attr_icon = "mdi:login" if action == ACTION_IN else "mdi:logout"
        self._attr_unique_id = f"{entry.entry_id}_kiosk_{action}"
        self._attr_device_info = hub_device(entry)

    @property
    def available(self) -> bool:
        session = self.punch_clock.session
        return (
            self.punch_clock.mode == MODE_EMPLOYEE
            and session is not None
            and session["on_the_clock"] != (self.action == ACTION_IN)
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.punch_clock.async_add_listener(self.async_write_ha_state))

    async def async_press(self) -> None:
        await self.punch_clock.async_session_punch(self.action)
