"""Polls QuickBooks Time for who is on the clock and where."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TSheetsApi, TSheetsAuthError, TSheetsError
from .const import (
    ACTIVE_LOOKBACK_DAYS,
    CONF_GPS_MAX_AGE,
    CONF_SCAN_INTERVAL,
    DEFAULT_GPS_MAX_AGE,
    DEFAULT_SCAN_INTERVAL,
    DIRECTORY_REFRESH,
    DOMAIN,
)
from .data import Employee, GeoPoint, Shift, TSheetsData

_LOGGER = logging.getLogger(__name__)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class TSheetsCoordinator(DataUpdateCoordinator[TSheetsData]):
    """Keeps employees, jobs, active shifts and GPS points up to date."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: TSheetsApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.api = api
        self._users: dict[int, Employee] = {}
        self._jobcodes: dict[int, dict] = {}
        self._directory_at: datetime | None = None
        self._locations: dict[int, GeoPoint] = {}
        self._geo_since: datetime | None = None
        self._geo_warned = False
        self._picture_cache: dict[str, str | None] = {}
        self._first_in: dict[int, datetime] = {}

    @property
    def gps_max_age(self) -> timedelta:
        return timedelta(
            minutes=self.config_entry.options.get(CONF_GPS_MAX_AGE, DEFAULT_GPS_MAX_AGE)
        )

    async def _async_update_data(self) -> TSheetsData:
        now = dt_util.utcnow()
        try:
            if self._directory_at is None or now - self._directory_at > DIRECTORY_REFRESH:
                await self._refresh_directory()
                self._directory_at = now
            shifts = await self.async_fetch_shifts()
        except TSheetsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TSheetsError as err:
            raise UpdateFailed(str(err)) from err

        await self._refresh_locations(now)
        await self._refresh_first_in(shifts)

        return TSheetsData(
            users=dict(self._users),
            jobcodes=dict(self._jobcodes),
            shifts=shifts,
            locations=dict(self._locations),
            first_in=dict(self._first_in),
        )

    async def _refresh_directory(self) -> None:
        users = await self.api.get_users()
        self._users = {
            _int(u.get("id")): Employee(
                id=_int(u.get("id")),
                first_name=str(u.get("first_name") or ""),
                last_name=str(u.get("last_name") or ""),
                email=str(u.get("email") or ""),
                picture=str(u.get("profile_image_url") or "") or None,
            )
            for u in users.values()
            if _int(u.get("id"))
        }
        for emp in self._users.values():
            emp.picture = await self._real_picture(emp.picture)

        jobcodes = await self.api.get_jobcodes()
        self._jobcodes = {_int(j.get("id")): j for j in jobcodes.values() if _int(j.get("id"))}

    async def _real_picture(self, url: str | None) -> str | None:
        """Drop placeholder avatars so the kiosk shows initials instead.

        QuickBooks Time falls back to a Gravatar URL for people without a photo, and
        Gravatar then serves its generic logo. Asking with d=404 tells us whether a
        real Gravatar exists.
        """
        if not url or "gravatar.com/avatar/" not in url:
            return url
        if url in self._picture_cache:
            return self._picture_cache[url]
        base = url.split("?", 1)[0]
        real = f"{base}?s=300" if await self.api.url_exists(f"{base}?d=404&s=80") else None
        self._picture_cache[url] = real
        return real

    async def async_fetch_shifts(self, user_ids: list[int] | None = None) -> dict[int, Shift]:
        """Fresh list of open timesheets (optionally for specific users)."""
        start_date = (dt_util.now().date() - timedelta(days=ACTIVE_LOOKBACK_DAYS)).isoformat()
        timesheets, supplemental = await self.api.get_active_timesheets(start_date, user_ids)

        # Make sure we can name any jobcode someone is clocked into.
        for job in supplemental.get("jobcodes", {}).values():
            jid = _int(job.get("id"))
            if jid and jid not in self._jobcodes:
                self._jobcodes[jid] = job

        shifts: dict[int, Shift] = {}
        for ts in timesheets.values():
            if ts.get("end") and not ts.get("on_the_clock"):
                continue
            uid = _int(ts.get("user_id"))
            if not uid:
                continue
            shifts[uid] = Shift(
                timesheet_id=_int(ts.get("id")),
                user_id=uid,
                start=dt_util.parse_datetime(str(ts.get("start") or "")),
                jobcode_id=_int(ts.get("jobcode_id")),
            )
        return shifts

    async def _refresh_first_in(self, shifts: dict[int, Shift]) -> None:
        """Earliest clock-in today for everyone on the clock. Failures are non-fatal."""
        if not shifts:
            self._first_in = {}
            return
        today = dt_util.now().date()
        try:
            timesheets = await self.api.get_timesheets_since(today.isoformat())
        except TSheetsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TSheetsError as err:
            _LOGGER.debug("Could not read today's timesheets: %s", err)
            return
        first: dict[int, datetime] = {}
        for ts in timesheets.values():
            uid = _int(ts.get("user_id"))
            start = dt_util.parse_datetime(str(ts.get("start") or ""))
            if not uid or start is None or dt_util.as_local(start).date() != today:
                continue
            if uid not in first or start < first[uid]:
                first[uid] = start
        self._first_in = first

    async def _refresh_locations(self, now: datetime) -> None:
        """GPS points recorded by the QuickBooks Time mobile app. Failures are non-fatal."""
        since = self._geo_since or (now - self.gps_max_age)
        try:
            geos = await self.api.get_geolocations(
                (since - timedelta(minutes=1)).replace(microsecond=0).isoformat()
            )
        except TSheetsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TSheetsError as err:
            if not self._geo_warned:
                _LOGGER.warning("Could not read GPS locations from QuickBooks Time: %s", err)
                self._geo_warned = True
            return

        self._geo_since = now
        for geo in geos.values():
            uid = _int(geo.get("user_id"))
            stamp = dt_util.parse_datetime(str(geo.get("created") or ""))
            try:
                lat = float(geo["latitude"])
                lon = float(geo["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not uid or stamp is None:
                continue
            current = self._locations.get(uid)
            if current is None or stamp > current.timestamp:
                self._locations[uid] = GeoPoint(
                    latitude=lat,
                    longitude=lon,
                    accuracy=float(geo.get("accuracy") or 0),
                    timestamp=stamp,
                )

        cutoff = now - self.gps_max_age
        self._locations = {u: p for u, p in self._locations.items() if p.timestamp >= cutoff}
