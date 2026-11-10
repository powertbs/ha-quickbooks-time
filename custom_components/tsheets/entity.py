"""Base entities."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import TSheetsCoordinator
from .data import Employee, Shift, TSheetsConfigEntry


def hub_device(entry: TSheetsConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="TSheets",
        manufacturer=MANUFACTURER,
        model="Time clock",
    )


class TSheetsEmployeeEntity(CoordinatorEntity[TSheetsCoordinator]):
    """An entity that belongs to one employee's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TSheetsCoordinator, user_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.user_id = user_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{user_id}_{key}"
        emp = coordinator.data.users[user_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{user_id}")},
            name=emp.name,
            manufacturer=MANUFACTURER,
            model="Employee",
            via_device=(DOMAIN, entry_id),
        )

    @property
    def available(self) -> bool:
        return super().available and self.user_id in self.coordinator.data.users

    @property
    def employee(self) -> Employee | None:
        return self.coordinator.data.users.get(self.user_id)

    @property
    def shift(self) -> Shift | None:
        return self.coordinator.data.shifts.get(self.user_id)

    def shift_attributes(self) -> dict:
        shift = self.shift
        data = self.coordinator.data
        return {
            "user_id": self.user_id,
            "clocked_in_since": shift.start.isoformat() if shift and shift.start else None,
            "job": data.job_path(shift.jobcode_id) if shift else None,
            "customer": data.customer_name(shift.jobcode_id) if shift else None,
            "jobcode_id": shift.jobcode_id if shift else None,
            "timesheet_id": shift.timesheet_id if shift else None,
        }


def add_employee_entities(
    entry: TSheetsConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[TSheetsCoordinator, int], Iterable[Entity]],
) -> None:
    """Add entities for every employee, and for employees added later in TSheets."""
    coordinator = entry.runtime_data.coordinator
    known: set[int] = set()

    @callback
    def _check() -> None:
        new = [uid for uid in coordinator.data.users if uid not in known]
        if new:
            known.update(new)
            async_add_entities([e for uid in new for e in factory(coordinator, uid)])

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
