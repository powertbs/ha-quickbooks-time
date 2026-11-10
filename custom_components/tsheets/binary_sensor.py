"""'On the clock' binary sensor per employee."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TSheetsCoordinator
from .data import TSheetsConfigEntry
from .entity import TSheetsEmployeeEntity, add_employee_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: TSheetsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    add_employee_entities(
        entry, async_add_entities, lambda c, uid: [OnTheClockSensor(c, uid)]
    )


class OnTheClockSensor(TSheetsEmployeeEntity, BinarySensorEntity):
    _attr_name = "On the clock"
    _attr_icon = "mdi:account-clock"

    def __init__(self, coordinator: TSheetsCoordinator, user_id: int) -> None:
        super().__init__(coordinator, user_id, "on_the_clock")

    @property
    def is_on(self) -> bool:
        return self.shift is not None

    @property
    def entity_picture(self) -> str | None:
        return self.coordinator.config_entry.runtime_data.photos.url(self.user_id, self.employee)

    @property
    def extra_state_attributes(self) -> dict:
        return self.shift_attributes()
