"""Sensors: current job per employee, plus hub sensors for the office dashboard."""
from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_GENDERS
from .coordinator import TSheetsCoordinator
from .data import TSheetsConfigEntry
from .entity import TSheetsEmployeeEntity, add_employee_entities, hub_device
from .punch import PunchClock

OFF_THE_CLOCK = "Off the clock"


async def async_setup_entry(
    hass: HomeAssistant, entry: TSheetsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        [
            ClockedInSensor(rt.coordinator, entry),
            KeypadDisplaySensor(rt.punch_clock, entry),
            KioskSensor(rt.punch_clock, entry),
            LastPunchSensor(rt.punch_clock, entry),
            TotalHoursSensor(rt.coordinator, entry, last_week=False),
            TotalHoursSensor(rt.coordinator, entry, last_week=True),
        ]
    )
    add_employee_entities(
        entry,
        async_add_entities,
        lambda c, uid: [
            CurrentJobSensor(c, uid),
            HoursSensor(c, uid, last_week=False),
            HoursSensor(c, uid, last_week=True),
        ],
    )


def _hhmm(seconds: int) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _hours(seconds: int) -> float:
    return round(seconds / 3600, 2)


def _week_start(coordinator: TSheetsCoordinator, last_week: bool) -> date:
    start = coordinator.this_week_start()
    return start - timedelta(days=7) if last_week else start


class _HoursMixin:
    """Shared setup for the weekly hours sensors."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _setup(self, last_week: bool) -> None:
        self.last_week = last_week
        self._attr_name = "Hours last week" if last_week else "Hours this week"
        self._attr_icon = "mdi:calendar-clock" if last_week else "mdi:clock-time-four-outline"


class HoursSensor(_HoursMixin, TSheetsEmployeeEntity, SensorEntity):
    """Hours one employee worked this week / last week (open shift counted live)."""

    def __init__(self, coordinator: TSheetsCoordinator, user_id: int, last_week: bool) -> None:
        super().__init__(
            coordinator, user_id, "hours_last_week" if last_week else "hours_this_week"
        )
        self._setup(last_week)

    def _worked(self) -> tuple[int, dict[date, int]]:
        return self.coordinator.data.worked_seconds(
            self.user_id, _week_start(self.coordinator, self.last_week), dt_util.utcnow()
        )

    @property
    def native_value(self) -> float:
        return _hours(self._worked()[0])

    @property
    def extra_state_attributes(self) -> dict:
        total, days = self._worked()
        start = _week_start(self.coordinator, self.last_week)
        return {
            "user_id": self.user_id,
            "formatted": _hhmm(total),
            "week_start": start.isoformat(),
            "week_end": (start + timedelta(days=6)).isoformat(),
            "on_the_clock": self.shift is not None,
            "days": {
                (start + timedelta(days=i)).strftime("%a %m/%d"): _hours(
                    days.get(start + timedelta(days=i), 0)
                )
                for i in range(7)
            },
        }


class TotalHoursSensor(_HoursMixin, CoordinatorEntity[TSheetsCoordinator], SensorEntity):
    """Hours everyone worked together this week / last week."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TSheetsCoordinator, entry: TSheetsConfigEntry, last_week: bool
    ) -> None:
        super().__init__(coordinator)
        key = "hours_last_week" if last_week else "hours_this_week"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = hub_device(entry)
        self._setup(last_week)

    def _per_employee(self) -> list[tuple[int, int]]:
        data = self.coordinator.data
        start = _week_start(self.coordinator, self.last_week)
        now = dt_util.utcnow()
        out = []
        for uid in data.users:
            secs, _ = data.worked_seconds(uid, start, now)
            if secs > 0:
                out.append((uid, secs))
        return sorted(out, key=lambda item: -item[1])

    @property
    def native_value(self) -> float:
        return _hours(sum(secs for _, secs in self._per_employee()))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        start = _week_start(self.coordinator, self.last_week)
        rows = self._per_employee()
        total = sum(secs for _, secs in rows)
        return {
            "formatted": _hhmm(total),
            "week_start": start.isoformat(),
            "week_end": (start + timedelta(days=6)).isoformat(),
            "people": len(rows),
            "employees": [
                {
                    "user_id": uid,
                    "name": data.users[uid].name,
                    "hours": _hours(secs),
                    "formatted": _hhmm(secs),
                }
                for uid, secs in rows
            ],
        }


class CurrentJobSensor(TSheetsEmployeeEntity, SensorEntity):
    """Which customer/job the employee is clocked into."""

    _attr_name = "Current job"
    _attr_icon = "mdi:briefcase-account"

    def __init__(self, coordinator: TSheetsCoordinator, user_id: int) -> None:
        super().__init__(coordinator, user_id, "current_job")

    @property
    def native_value(self) -> str:
        shift = self.shift
        if shift is None:
            return OFF_THE_CLOCK
        return self.coordinator.data.job_path(shift.jobcode_id) or "On the clock (no job)"

    @property
    def extra_state_attributes(self) -> dict:
        return self.shift_attributes()


class ClockedInSensor(CoordinatorEntity[TSheetsCoordinator], SensorEntity):
    """How many people are on the clock, with the list as an attribute."""

    _attr_has_entity_name = True
    _attr_name = "Clocked in"
    _attr_icon = "mdi:account-group"
    _attr_native_unit_of_measurement = "employees"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TSheetsCoordinator, entry: TSheetsConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_clocked_in"
        self._attr_device_info = hub_device(entry)
        self._entry = entry

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.shifts)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        photos = self._entry.runtime_data.photos
        genders: dict[str, str] = self._entry.options.get(CONF_GENDERS, {})
        employees = []
        for uid, shift in data.shifts.items():
            emp = data.users.get(uid)
            loc = data.locations.get(uid)
            first_in = data.first_in.get(uid) or shift.start
            employees.append(
                {
                    "user_id": uid,
                    "name": emp.name if emp else f"User {uid}",
                    "first_name": emp.first_name if emp else None,
                    "picture": photos.url(uid, emp),
                    "gender": genders.get(str(uid)),
                    "first_in": first_in.isoformat() if first_in else None,
                    "job": data.job_path(shift.jobcode_id),
                    "customer": data.customer_name(shift.jobcode_id),
                    "since": shift.start.isoformat() if shift.start else None,
                    "latitude": loc.latitude if loc else None,
                    "longitude": loc.longitude if loc else None,
                }
            )
        employees.sort(key=lambda e: e["name"].lower())
        return {
            "employees": employees,
            "names": ", ".join(e["name"] for e in employees),
        }


class _PunchClockSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, punch_clock: PunchClock, entry: TSheetsConfigEntry, key: str) -> None:
        self.punch_clock = punch_clock
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = hub_device(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.punch_clock.async_add_listener(self.async_write_ha_state))


class KeypadDisplaySensor(_PunchClockSensor):
    """What the keypad screen should show (●●, result message, errors)."""

    _attr_name = "Keypad display"
    _attr_icon = "mdi:dialpad"

    def __init__(self, punch_clock: PunchClock, entry: TSheetsConfigEntry) -> None:
        super().__init__(punch_clock, entry, "keypad_display")

    @property
    def native_value(self) -> str:
        return self.punch_clock.display


class KioskSensor(_PunchClockSensor):
    """Drives the kiosk dashboard.

    State is the screen mode: idle | typing | working | employee | result | error.
    Attributes describe the employee on screen.
    """

    _attr_name = "Kiosk"
    _attr_icon = "mdi:tablet-dashboard"

    def __init__(self, punch_clock: PunchClock, entry: TSheetsConfigEntry) -> None:
        super().__init__(punch_clock, entry, "kiosk")

    @property
    def native_value(self) -> str:
        return self.punch_clock.mode

    @property
    def entity_picture(self) -> str | None:
        session = self.punch_clock.session
        return session.get("picture") if session else None

    @property
    def extra_state_attributes(self) -> dict:
        session = self.punch_clock.session or {}
        return {
            "display": self.punch_clock.display,
            "user_id": session.get("user_id"),
            "employee_name": session.get("name"),
            "first_name": session.get("first_name"),
            "picture": session.get("picture"),
            "on_the_clock": session.get("on_the_clock"),
            "status": session.get("status"),
            "since": session.get("since"),
            "job": session.get("job"),
        }


class LastPunchSensor(_PunchClockSensor):
    """The most recent clock in / clock out done through Home Assistant."""

    _attr_name = "Last punch"
    _attr_icon = "mdi:clock-check"

    def __init__(self, punch_clock: PunchClock, entry: TSheetsConfigEntry) -> None:
        super().__init__(punch_clock, entry, "last_punch")

    @property
    def native_value(self) -> str | None:
        last = self.punch_clock.last_punch
        return last["message"][:255] if last else None

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self.punch_clock.last_punch or {})
