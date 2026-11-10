"""Kiosk logic for the shared office dashboard.

Flow:  PIN keypad  ->  employee screen (photo, name, status, Clock In / Clock Out)
       ->  confirmation  ->  back to the keypad.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import TSheetsError
from .const import (
    ACTION_IN,
    ACTION_OUT,
    ACTION_TOGGLE,
    CONF_DEFAULT_JOBCODE,
    CONF_PINS,
    DEFAULT_JOB_NAME,
    ERROR_DISPLAY_SECONDS,
    EVENT_KIOSK_IDLE,
    MODE_CHECKING,
    EVENT_PUNCH,
    EVENT_PUNCH_FAILED,
    KEYPAD_IDLE_TEXT,
    KEYPAD_TIMEOUT,
    LOCKOUT_SECONDS,
    MAX_FAILED_ATTEMPTS,
    MODE_EMPLOYEE,
    MODE_ERROR,
    MODE_IDLE,
    MODE_RESULT,
    MODE_TYPING,
    MODE_WORKING,
    PIN_LENGTH,
    POPUP_IDLE_CLOSE,
    RESULT_DISPLAY_SECONDS,
    SESSION_TIMEOUT,
)
from .coordinator import TSheetsCoordinator
from .data import Employee, Shift
from .photos import PhotoStore

_LOGGER = logging.getLogger(__name__)


class PunchError(Exception):
    """A punch was refused; the message is safe to show on the dashboard."""


def _fmt_duration(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _fmt_time(when: datetime) -> str:
    return dt_util.as_local(when).strftime("%I:%M %p").lstrip("0")


class PunchClock:
    """State machine behind the kiosk screen, plus the actual clock in / out calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: TSheetsCoordinator,
        photos: PhotoStore,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.photos = photos
        self.mode: str = MODE_IDLE
        self.display: str = KEYPAD_IDLE_TEXT
        self.session: dict[str, Any] | None = None  # the employee on screen
        self.last_punch: dict[str, Any] | None = None
        self._buffer = ""
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._screen_unsub: CALLBACK_TYPE | None = None
        self._buffer_unsub: CALLBACK_TYPE | None = None
        self._idle_unsub: CALLBACK_TYPE | None = None
        self._popup_idle_close = False
        self._failed_attempts = 0
        self._locked_until: datetime | None = None

    # ----- listeners (sensors / buttons) -------------------------------

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def async_shutdown(self) -> None:
        self._cancel_screen_timer()
        self._cancel_idle_close()
        self._clear_buffer()
        self._listeners.clear()

    # ----- pop-up auto-close -------------------------------------------

    @callback
    def _cancel_idle_close(self) -> None:
        if self._idle_unsub:
            self._idle_unsub()
            self._idle_unsub = None

    @callback
    def _arm_idle_close(self) -> None:
        """(Re)start the countdown that closes an untouched PIN-screen pop-up."""
        self._cancel_idle_close()

        @callback
        def _fire(_now) -> None:
            self._idle_unsub = None
            if not self._popup_idle_close:
                return
            if self.mode not in (MODE_IDLE, MODE_TYPING, MODE_ERROR):
                return  # someone is on their employee screen
            self._popup_idle_close = False
            self._go_idle()
            self.hass.bus.async_fire(EVENT_KIOSK_IDLE)
            if self.hass.services.has_service("browser_mod", "close_popup"):
                self.hass.async_create_task(
                    self.hass.services.async_call("browser_mod", "close_popup", {})
                )

        self._idle_unsub = async_call_later(self.hass, POPUP_IDLE_CLOSE, _fire)

    # ----- screen state ------------------------------------------------

    @callback
    def _cancel_screen_timer(self) -> None:
        if self._screen_unsub:
            self._screen_unsub()
            self._screen_unsub = None

    @callback
    def _set_screen(self, mode: str, text: str, reset_after: float | None = None) -> None:
        self._cancel_screen_timer()
        self.mode = mode
        self.display = text
        if reset_after:

            @callback
            def _reset(_now) -> None:
                self._screen_unsub = None
                self._go_idle()

            self._screen_unsub = async_call_later(self.hass, reset_after, _reset)
        self._notify()

    @callback
    def _go_idle(self) -> None:
        self._clear_buffer()
        self.session = None
        self._set_screen(MODE_IDLE, KEYPAD_IDLE_TEXT)

    @callback
    def _clear_buffer(self) -> None:
        self._buffer = ""
        if self._buffer_unsub:
            self._buffer_unsub()
            self._buffer_unsub = None

    # ----- helpers -----------------------------------------------------

    def _pin_map(self) -> dict[str, int]:
        pins: dict[str, str] = self.entry.options.get(CONF_PINS, {})
        return {str(pin): int(uid) for uid, pin in pins.items() if pin}

    def _employee(self, user_id: int) -> Employee | None:
        data = self.coordinator.data
        return data.users.get(user_id) if data else None

    def _name(self, user_id: int) -> str:
        emp = self._employee(user_id)
        return emp.name if emp else f"User {user_id}"

    def _locked_seconds(self) -> int:
        if self._locked_until is None:
            return 0
        remaining = (self._locked_until - dt_util.utcnow()).total_seconds()
        if remaining <= 0:
            self._locked_until = None
            return 0
        return int(remaining) + 1

    def _check_pin(self, pin: str) -> int:
        """Return the user id for a PIN, enforcing the wrong-PIN lockout."""
        if locked := self._locked_seconds():
            raise PunchError(f"Too many wrong PINs — try again in {locked}s")
        user_id = self._pin_map().get(str(pin).strip())
        if user_id is None:
            self._failed_attempts += 1
            if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                self._failed_attempts = 0
                self._locked_until = dt_util.utcnow() + timedelta(seconds=LOCKOUT_SECONDS)
            self.hass.bus.async_fire(EVENT_PUNCH_FAILED, {"reason": "invalid_pin"})
            raise PunchError("PIN not recognized")
        self._failed_attempts = 0
        return user_id

    def _status(self, on_the_clock: bool, since: datetime | None) -> str:
        if not on_the_clock:
            return "You are clocked OUT now"
        text = "You are clocked IN now"
        if since:
            text += f" · since {_fmt_time(since)}"
        return text

    # ----- keypad ------------------------------------------------------

    async def async_press(self, key: str) -> None:
        """Handle one button press from the kiosk dashboard."""
        if key in ("clock_in", "clock_out"):
            await self.async_session_punch(ACTION_IN if key == "clock_in" else ACTION_OUT)
            return
        if key == "cancel":
            self._cancel_idle_close()
            self._go_idle()
            return
        if key in ("open", "wake"):
            # "open"  = a browser_mod pop-up opened: the integration closes it after
            #           POPUP_IDLE_CLOSE s untouched (browser_mod.close_popup).
            # "wake"  = the built-in Time Clock card opened: the card closes itself.
            self._popup_idle_close = key == "open"
            if self._popup_idle_close:
                self._arm_idle_close()
            else:
                self._cancel_idle_close()
            if self.mode not in (MODE_WORKING, MODE_CHECKING, MODE_EMPLOYEE):
                self._go_idle()  # fresh PIN screen every time the pop-up opens
            return
        # Keypad activity inside a browser_mod pop-up restarts its auto-close countdown.
        if self._popup_idle_close:
            self._arm_idle_close()
        if key == "clear":
            self._go_idle()
            return
        if self.mode in (MODE_WORKING, MODE_CHECKING):
            return

        if locked := self._locked_seconds():
            self._clear_buffer()
            self._set_screen(MODE_ERROR, f"Locked — try again in {locked}s", ERROR_DISPLAY_SECONDS)
            return

        if key == "back":
            if self.mode == MODE_TYPING:
                self._buffer = self._buffer[:-1]
                if self._buffer:
                    self._set_screen(MODE_TYPING, "●" * len(self._buffer))
                else:
                    self._go_idle()
            return

        if key == "enter":
            if len(self._buffer) == PIN_LENGTH:
                await self._identify()
            else:
                self._clear_buffer()
                self._set_screen(
                    MODE_ERROR, f"Enter your {PIN_LENGTH}-digit PIN", ERROR_DISPLAY_SECONDS
                )
            return

        if not (len(key) == 1 and key.isdigit()):
            return

        # A digit while someone else's screen is up starts a fresh PIN.
        if self.mode != MODE_TYPING:
            self._clear_buffer()
            self.session = None
        if len(self._buffer) >= PIN_LENGTH:
            return
        self._buffer += key

        if self._buffer_unsub:
            self._buffer_unsub()

        @callback
        def _timeout(_now) -> None:
            self._buffer_unsub = None
            if self.mode == MODE_TYPING:
                self._go_idle()

        self._buffer_unsub = async_call_later(self.hass, KEYPAD_TIMEOUT, _timeout)

        if len(self._buffer) == PIN_LENGTH:
            await self._identify()
        else:
            self._set_screen(MODE_TYPING, "●" * len(self._buffer))

    async def _identify(self) -> None:
        """PIN complete: look the employee up and show their screen."""
        pin = self._buffer
        self._clear_buffer()
        self._set_screen(MODE_CHECKING, "Checking…")
        try:
            user_id = self._check_pin(pin)
            try:
                shifts = await self.coordinator.async_fetch_shifts([user_id])
            except TSheetsError as err:
                raise PunchError(f"Can't reach QuickBooks Time ({err})") from err
        except PunchError as err:
            self._set_screen(MODE_ERROR, f"✖ {err}", ERROR_DISPLAY_SECONDS)
            return

        shift: Shift | None = shifts.get(user_id)
        data = self.coordinator.data
        emp = self._employee(user_id)
        on = shift is not None
        job = data.job_path(shift.jobcode_id) if on and data else None
        self.session = {
            "user_id": user_id,
            "name": self._name(user_id),
            "first_name": emp.first_name if emp else self._name(user_id),
            "picture": self.photos.url(user_id, emp),
            "on_the_clock": on,
            "since": shift.start.isoformat() if on and shift.start else None,
            "job": job,
            "status": self._status(on, shift.start if shift else None),
        }
        self._cancel_idle_close()  # the employee screen has its own 30 s timeout
        self._set_screen(MODE_EMPLOYEE, f"Hi {self.session['first_name']}!", SESSION_TIMEOUT)

    async def async_session_punch(self, action: str) -> None:
        """Clock In / Clock Out button on the employee screen."""
        session = self.session
        if session is None or self.mode != MODE_EMPLOYEE:
            return
        if (action == ACTION_IN) == session["on_the_clock"]:
            return  # that button is grayed out
        self._set_screen(MODE_WORKING, "Working…")
        try:
            result = await self.async_punch_user(session["user_id"], action)
        except PunchError as err:
            self._set_screen(MODE_ERROR, f"✖ {err}", ERROR_DISPLAY_SECONDS)
            return

        when = dt_util.parse_datetime(result["time"])
        if action == ACTION_IN:
            session.update(
                on_the_clock=True,
                since=result["time"],
                job=result.get("job"),
                status=f"✔ You are now clocked IN at {_fmt_time(when)}",
            )
        else:
            session.update(
                on_the_clock=False,
                since=None,
                job=None,
                status=f"✔ You are now clocked OUT at {_fmt_time(when)}"
                + (f" · worked {result['worked']}" if result.get("worked") else ""),
            )
        self._set_screen(MODE_RESULT, result["message"], RESULT_DISPLAY_SECONDS)

    # ----- punching (also used by services) ----------------------------

    async def async_punch(
        self, pin: str, action: str = ACTION_TOGGLE, jobcode_id: int | None = None
    ) -> dict[str, Any]:
        """Identify the employee by PIN and clock them in or out in one step."""
        return await self.async_punch_user(self._check_pin(pin), action, jobcode_id)

    async def async_punch_user(
        self, user_id: int, action: str = ACTION_TOGGLE, jobcode_id: int | None = None
    ) -> dict[str, Any]:
        """Clock a known employee in/out."""
        name = self._name(user_id)
        async with self._lock:
            try:
                shifts = await self.coordinator.async_fetch_shifts([user_id])
            except TSheetsError as err:
                raise PunchError(f"Can't reach QuickBooks Time ({err})") from err
            shift = shifts.get(user_id)

            if action == ACTION_TOGGLE:
                action = ACTION_OUT if shift else ACTION_IN

            try:
                if action == ACTION_IN:
                    if shift:
                        raise PunchError(f"{name} is already clocked in")
                    result = await self._clock_in(user_id, name, jobcode_id)
                else:
                    if not shift:
                        raise PunchError(f"{name} is not clocked in")
                    result = await self._clock_out(user_id, name, shift)
            except TSheetsError as err:
                self.hass.bus.async_fire(
                    EVENT_PUNCH_FAILED,
                    {"reason": "api_error", "user_id": user_id, "name": name, "error": str(err)},
                )
                raise PunchError(f"QuickBooks Time refused it: {err}") from err

        self.last_punch = result
        self.hass.bus.async_fire(EVENT_PUNCH, result)
        self._notify()
        await self.coordinator.async_request_refresh()
        return result

    async def _clock_in(self, user_id: int, name: str, jobcode_id: int | None) -> dict[str, Any]:
        data = self.coordinator.data
        if jobcode_id is None:
            jobcode_id = int(self.entry.options.get(CONF_DEFAULT_JOBCODE, 0) or 0)
        if not jobcode_id and data:
            jobcode_id = data.find_jobcode(DEFAULT_JOB_NAME)
        # No "Office" job and none picked in settings: clock in without a job
        # (fine for companies that don't track jobs; QuickBooks Time rejects it if jobs are required).
        jobcode_id = jobcode_id or 0
        now = dt_util.now().replace(microsecond=0)
        item = await self.coordinator.api.clock_in(
            user_id, jobcode_id, now.isoformat(), notes="Clocked in from Home Assistant"
        )
        job = data.job_path(jobcode_id) if data else None
        message = f"✔ {name} clocked IN at {_fmt_time(now)}"
        if job:
            message += f" · {job}"
        return {
            "action": "clock_in",
            "user_id": user_id,
            "name": name,
            "time": now.isoformat(),
            "jobcode_id": jobcode_id,
            "job": job,
            "timesheet_id": item.get("id"),
            "message": message,
        }

    async def _clock_out(self, user_id: int, name: str, shift: Shift) -> dict[str, Any]:
        now = dt_util.now().replace(microsecond=0)
        await self.coordinator.api.clock_out(shift.timesheet_id, now.isoformat())
        worked = _fmt_duration(now - shift.start) if shift.start else None
        message = f"✔ {name} clocked OUT at {_fmt_time(now)}"
        if worked:
            message += f" · worked {worked}"
        data = self.coordinator.data
        return {
            "action": "clock_out",
            "user_id": user_id,
            "name": name,
            "time": now.isoformat(),
            "jobcode_id": shift.jobcode_id,
            "job": data.job_path(shift.jobcode_id) if data else None,
            "timesheet_id": shift.timesheet_id,
            "worked": worked,
            "message": message,
        }
