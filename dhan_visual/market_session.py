"""Local NSE refresh window; never requests a calendar or broker quote."""
from datetime import datetime, time
from .ce_eod_remote import IST, KNOWN_NSE_HOLIDAYS

OPEN = time(9, 0)
CLOSE = time(15, 30)
CLOSED_MESSAGE = "Market closed · showing last saved data"


def is_refresh_open(now=None):
    """Weekdays 09:00 inclusive to 15:30 exclusive, in exchange time."""
    now = datetime.now(IST) if now is None else now.astimezone(IST)
    return (now.weekday() < 5 and now.date() not in KNOWN_NSE_HOLIDAYS
            and OPEN <= now.time() < CLOSE)


def session_day():
    return datetime.now(IST).date().isoformat()
