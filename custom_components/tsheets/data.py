"""Data models shared across the integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .api import TSheetsApi
    from .coordinator import TSheetsCoordinator
    from .photos import PhotoStore
    from .punch import PunchClock


@dataclass
class Employee:
    id: int
    first_name: str
    last_name: str
    email: str = ""
    picture: str | None = None  # QuickBooks Time profile image URL

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or f"User {self.id}"


@dataclass
class Shift:
    timesheet_id: int
    user_id: int
    start: datetime | None
    jobcode_id: int


@dataclass
class GeoPoint:
    latitude: float
    longitude: float
    accuracy: float
    timestamp: datetime


@dataclass
class WeekHours:
    """Finished (closed) timesheet seconds, per user and per day, for two weeks."""

    this_week_start: date
    # {user_id: {date: seconds}} - only timesheets that are no longer on the clock
    done: dict[int, dict[date, int]] = field(default_factory=dict)

    @property
    def last_week_start(self) -> date:
        return self.this_week_start - timedelta(days=7)


@dataclass
class TSheetsData:
    users: dict[int, Employee] = field(default_factory=dict)
    jobcodes: dict[int, dict] = field(default_factory=dict)
    shifts: dict[int, Shift] = field(default_factory=dict)  # keyed by user_id
    locations: dict[int, GeoPoint] = field(default_factory=dict)  # keyed by user_id
    first_in: dict[int, datetime] = field(default_factory=dict)  # earliest clock-in today
    hours: WeekHours | None = None

    def worked_seconds(
        self, user_id: int, week_start: date, now: datetime
    ) -> tuple[int, dict[date, int]]:
        """Seconds worked in the 7 days from week_start, total and per day.

        Finished timesheets count on their QuickBooks Time date. A shift that is
        still open counts live, split at midnight, up to now.
        """
        week_end = week_start + timedelta(days=7)
        days: dict[date, int] = {}
        if self.hours:
            for day, secs in self.hours.done.get(user_id, {}).items():
                if week_start <= day < week_end:
                    days[day] = days.get(day, 0) + secs

        shift = self.shifts.get(user_id)
        if shift and shift.start:
            cursor = dt_util.as_local(shift.start)
            end = dt_util.as_local(now)
            while cursor < end:
                midnight = dt_util.start_of_local_day(cursor.date() + timedelta(days=1))
                piece_end = min(midnight, end)
                if week_start <= cursor.date() < week_end:
                    secs = int((piece_end - cursor).total_seconds())
                    days[cursor.date()] = days.get(cursor.date(), 0) + secs
                cursor = piece_end
        return sum(days.values()), days

    def _chain(self, jobcode_id: int) -> list[dict]:
        """Jobcodes from top-level (customer) down to this one."""
        chain: list[dict] = []
        seen: set[int] = set()
        current = self.jobcodes.get(jobcode_id)
        while current and int(current.get("id", 0)) not in seen:
            seen.add(int(current["id"]))
            chain.insert(0, current)
            current = self.jobcodes.get(int(current.get("parent_id") or 0))
        return chain

    def job_path(self, jobcode_id: int | None) -> str | None:
        if not jobcode_id:
            return None
        chain = self._chain(jobcode_id)
        if not chain:
            return f"Job {jobcode_id}"
        return " › ".join(str(j.get("name", "")) for j in chain)

    def customer_name(self, jobcode_id: int | None) -> str | None:
        if not jobcode_id:
            return None
        chain = self._chain(jobcode_id)
        return str(chain[0].get("name")) if chain else None

    def job_name(self, jobcode_id: int | None) -> str | None:
        if not jobcode_id:
            return None
        job = self.jobcodes.get(jobcode_id)
        return str(job.get("name")) if job else f"Job {jobcode_id}"

    def find_jobcode(self, name: str) -> int | None:
        """Active regular jobcode with this name; prefers a top-level customer."""
        matches = [
            (jid, job)
            for jid, job in self.jobcodes.items()
            if str(job.get("name", "")).strip().lower() == name.strip().lower()
            and job.get("active", True)
            and job.get("type", "regular") == "regular"
        ]
        matches.sort(key=lambda m: (bool(int(m[1].get("parent_id") or 0)), m[0]))
        return matches[0][0] if matches else None

    def clockable_jobcodes(self) -> list[tuple[int, str]]:
        """Active, regular, leaf jobcodes — the ones you can clock into."""
        out = [
            (jid, self.job_path(jid) or str(jid))
            for jid, job in self.jobcodes.items()
            if job.get("active", True)
            and job.get("type", "regular") == "regular"
            and not job.get("has_children")
        ]
        return sorted(out, key=lambda item: item[1].lower())


@dataclass
class TSheetsRuntime:
    api: TSheetsApi
    coordinator: TSheetsCoordinator
    punch_clock: PunchClock
    photos: PhotoStore


type TSheetsConfigEntry = ConfigEntry[TSheetsRuntime]
