"""GPS location per employee, from the QuickBooks Time mobile app."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import TSheetsCoordinator
from .data import GeoPoint, TSheetsConfigEntry
from .entity import TSheetsEmployeeEntity, add_employee_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: TSheetsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    add_employee_entities(entry, async_add_entities, lambda c, uid: [EmployeeTracker(c, uid)])


class EmployeeTracker(TSheetsEmployeeEntity, TrackerEntity):
    _attr_name = "Location"
    _attr_icon = "mdi:map-marker-account"

    def __init__(self, coordinator: TSheetsCoordinator, user_id: int) -> None:
        super().__init__(coordinator, user_id, "location")

    @property
    def _point(self) -> GeoPoint | None:
        point = self.coordinator.data.locations.get(self.user_id)
        if point and dt_util.utcnow() - point.timestamp <= self.coordinator.gps_max_age:
            return point
        return None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return p.latitude if (p := self._point) else None

    @property
    def longitude(self) -> float | None:
        return p.longitude if (p := self._point) else None

    @property
    def location_accuracy(self) -> float:
        return p.accuracy if (p := self._point) else 0

    @property
    def extra_state_attributes(self) -> dict:
        p = self._point
        return {
            **self.shift_attributes(),
            "on_the_clock": self.shift is not None,
            "gps_time": p.timestamp.isoformat() if p else None,
        }
