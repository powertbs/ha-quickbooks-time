"""Data models shared across the integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

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
class TSheetsData:
    users: dict[int, Employee] = field(default_factory=dict)
    jobcodes: dict[int, dict] = field(default_factory=dict)
    shifts: dict[int, Shift] = field(default_factory=dict)  # keyed by user_id
    locations: dict[int, GeoPoint] = field(default_factory=dict)  # keyed by user_id
    first_in: dict[int, datetime] = field(default_factory=dict)  # earliest clock-in today

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
