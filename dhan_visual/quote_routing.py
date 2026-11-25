"""Conservative Dhan-first cash quote routing with Yahoo fallback.

A Yahoo fallback is per *missing stock* and is never represented as a Dhan
quote. The automatic route only uses authenticated, verified accounts; all
other users retain the free Yahoo screener. Rate-limit failures back off.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta, timezone


AUTO_SOURCE = "Auto (Dhan → Yahoo)"
DEFAULT_DHAN_SECONDS = 300  # 5 minutes between full cash LTP snapshots
DHAN_LIMIT_BACKOFF_SECONDS = 1800  # 30 minutes after an HTTP 429
OFF_HOURS_SECONDS = 3600
IST = timezone(timedelta(hours=5, minutes=30))


def nse_regular_session(now: datetime | None = None) -> bool:
    """Conservative regular-hours gate (not a substitute for holiday calendar)."""
    instant = now or datetime.now(IST)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=IST)
    local = instant.astimezone(IST)
    return (
        local.weekday() < 5
        and time(9, 15) <= local.time() <= time(15, 30)
    )



def choose_cash_source(choice: str, *, verified: bool, token_available: bool) -> str:
    if choice == AUTO_SOURCE:
        return "Dhan" if verified and token_available else "Yahoo Finance"
    if choice in ("FYERS", "Dhan", "Yahoo Finance"):
        return choice
    raise ValueError("Unsupported quote source")


def merge_dhan_yahoo(dhan_quotes: Mapping, yahoo_quotes: Mapping) -> dict:
    """Broker wins only for symbols with actual returned Dhan prices."""
    combined = dict(yahoo_quotes)
    combined.update(dhan_quotes)
    return combined


def missing_underlyings(underlyings, dhan_quotes: Mapping):
    return [row for row in underlyings if row.entry.symbol not in dhan_quotes]


def retry_window_seconds(errors: list[str] | tuple[str, ...]) -> int:
    """Back off if Dhan explicitly refuses rate-limited requests."""
    return (DHAN_LIMIT_BACKOFF_SECONDS
            if any("429" in str(error) or "rate limit" in str(error).lower()
                   for error in errors)
            else DEFAULT_DHAN_SECONDS)


def should_fetch_again(last_attempt: float | None, *, now: float,
                       errors: list[str] | tuple[str, ...] = (),
                       manual: bool = False, market_open: bool = True) -> bool:
    if last_attempt is None:
        return True
    if now < last_attempt:
        return False
    window = max(
        retry_window_seconds(errors),
        DEFAULT_DHAN_SECONDS if market_open else OFF_HOURS_SECONDS,
    )
    # User refresh is allowed, but NOT when broker returned rate limit.
    if manual and window != DHAN_LIMIT_BACKOFF_SECONDS:
        return now - last_attempt >= 5
    return now - last_attempt >= window
