"""Polls QuickBooks Time for who is on the clock and where."""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
    CONF_WEEK_START,
    DEFAULT_GPS_MAX_AGE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WEEK_START,
    DIRECTORY_REFRESH,
    DOMAIN,
    FALLBACK_WEEK_START,
    HOURS_REFRESH,
    WEEK_START_AUTO,
)
from .data import Employee, GeoPoint, Shift, TSheetsData, WeekHours

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
        self._hours: WeekHours | None = None
        self._hours_at: datetime | None = None
        self._company_week_start: int | None = None  # Python weekday, from QuickBooks Time

    @property
    def gps_max_age(self) -> timedelta:
        return timedelta(
            minutes=self.config_entry.options.get(CONF_GPS_MAX_AGE, DEFAULT_GPS_MAX_AGE)
        )

    @property
    def week_start_day(self) -> int:
        """Python weekday the work week starts on (0 = Monday ... 6 = Sunday)."""
        chosen = int(self.config_entry.options.get(CONF_WEEK_START, DEFAULT_WEEK_START))
        if chosen != WEEK_START_AUTO:
            return chosen
        if self._company_week_start is not None:
            return self._company_week_start
        return FALLBACK_WEEK_START

    def this_week_start(self) -> date:
        today = dt_util.now().date()
        return today - timedelta(days=(today.weekday() - self.week_start_day) % 7)

    def invalidate_hours(self) -> None:
        """Re-read finished timesheets on the next refresh (e.g. right after a punch)."""
        self._hours_at = None

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
        await self._refresh_history(now)

        return TSheetsData(
            users=dict(self._users),
            jobcodes=dict(self._jobcodes),
            shifts=shifts,
            locations=dict(self._locations),
            first_in=dict(self._first_in),
            hours=self._hours,
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

        try:
            tsheets_day = await self.api.get_week_start()
        except TSheetsAuthError:
            raise
        except TSheetsError as err:
            _LOGGER.debug("Could not read the week start from QuickBooks Time: %s", err)
        else:
            if tsheets_day is not None and 0 <= tsheets_day <= 6:
                # QuickBooks Time counts 0 = Sunday ... 6 = Saturday; Python 0 = Monday.
                self._company_week_start = (tsheets_day - 1) % 7

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

    async def _refresh_history(self, now: datetime) -> None:
        """This week's and last week's timesheets: weekly hours + first clock-in today.

        Re-read every HOURS_REFRESH (and right after a punch). Failures are non-fatal;
        the previous numbers are kept.
        """
        week_start = self.this_week_start()
        if (
            self._hours_at is not None
            and self._hours is not None
            and self._hours.this_week_start == week_start
            and now - self._hours_at < HOURS_REFRESH
        ):
            return
        last_week_start = week_start - timedelta(days=7)
        try:
            timesheets = await self.api.get_timesheets_since(last_week_start.isoformat())
        except TSheetsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TSheetsError as err:
            _LOGGER.warning("Could not read timesheets for the hours sensors: %s", err)
            return

        today = dt_util.now().date()
        done: dict[int, dict[date, int]] = {}
        first: dict[int, datetime] = {}
        for ts in timesheets.values():
            uid = _int(ts.get("user_id"))
            if not uid:
                continue
            start = dt_util.parse_datetime(str(ts.get("start") or ""))
            if start is not None and dt_util.as_local(start).date() == today:
                if uid not in first or start < first[uid]:
                    first[uid] = start
            if ts.get("on_the_clock"):
                continue  # open shift: counted live from the active shift
            day = dt_util.parse_date(str(ts.get("date") or ""))
            if day is None and start is not None:
                day = dt_util.as_local(start).date()
            secs = _int(ts.get("duration"))
            if day is None or secs <= 0:
                continue
            per_day = done.setdefault(uid, {})
            per_day[day] = per_day.get(day, 0) + secs

        self._hours = WeekHours(this_week_start=week_start, done=done)
        self._first_in = first
        self._hours_at = now

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
