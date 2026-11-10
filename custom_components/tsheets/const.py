"""Constants for the TSheets (QuickBooks Time) integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "tsheets"
MANUFACTURER = "QuickBooks Time"

API_BASE = "https://rest.tsheets.com/api/v1"

# Dashboard cards shipped with the integration (frontend/tsheets-cards.js).
CARDS_URL = f"/{DOMAIN}_static/tsheets-cards.js"
CARDS_VERSION = "0.0.2"  # bump with the JS so browsers fetch the new file

# Options
CONF_PINS = "pins"  # {str(user_id): "1234"}
CONF_DEFAULT_JOBCODE = "default_jobcode_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_GPS_MAX_AGE = "gps_max_age"
CONF_GENDERS = "genders"  # {str(user_id): "male" | "female"} for the placeholder avatar

# Keypad clock-ins go to the customer/job with this name unless another is picked in settings.
DEFAULT_JOB_NAME = "Office"

DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_GPS_MAX_AGE = 120  # minutes

# How often to re-download the employee list and job/customer list.
DIRECTORY_REFRESH = timedelta(hours=1)
# How far back to look for timesheets that are still on the clock.
ACTIVE_LOOKBACK_DAYS = 31

# Keypad behaviour
PIN_LENGTH = 4
KEYPAD_IDLE_TEXT = "Enter your PIN"
KEYPAD_TIMEOUT = 20  # seconds before a half-typed PIN is cleared
SESSION_TIMEOUT = 30  # seconds the employee screen stays up without a button press
RESULT_DISPLAY_SECONDS = 3
ERROR_DISPLAY_SECONDS = 4
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
# Close the Time Clock pop-up when nobody touches the PIN screen for this long.
POPUP_IDLE_CLOSE = 8

# Kiosk screen modes (state of sensor.tsheets_kiosk)
MODE_IDLE = "idle"  # PIN keypad, nothing typed
MODE_TYPING = "typing"  # PIN keypad, some digits typed
MODE_CHECKING = "checking"  # PIN typed, looking the employee up
MODE_WORKING = "working"  # clocking in/out with QuickBooks Time
MODE_EMPLOYEE = "employee"  # photo + name + Clock In / Clock Out buttons
MODE_RESULT = "result"  # "You are now clocked IN" confirmation
MODE_ERROR = "error"  # wrong PIN etc.

# Local photo override: /config/www/tsheets/<user_id or first_last>.<ext>
PHOTO_DIR = "tsheets"
PHOTO_EXTENSIONS = ("jpg", "jpeg", "png", "webp")

ACTION_TOGGLE = "toggle"
ACTION_IN = "in"
ACTION_OUT = "out"

KEYS = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "back", "clear", "enter", "cancel", "clock_in", "clock_out", "open", "wake",
]

EVENT_PUNCH = f"{DOMAIN}_punch"
EVENT_PUNCH_FAILED = f"{DOMAIN}_punch_failed"
EVENT_KIOSK_IDLE = f"{DOMAIN}_kiosk_idle"  # PIN screen untouched for POPUP_IDLE_CLOSE s

SERVICE_PUNCH = "punch"
SERVICE_KEYPAD = "keypad"
SERVICE_CLOCK_IN = "clock_in"
SERVICE_CLOCK_OUT = "clock_out"
