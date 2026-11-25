"""Conservative Dhan-first cash quote routing with Yahoo fallback.

A Yahoo fallback is per *missing stock* and is never represented as a Dhan
quote. The automatic route only uses authenticated, verified accounts; all
other users retain the free Yahoo screener. Rate-limit failures back off.
"""
from __future__ import annotations

from collections.abc import Mapping


AUTO_SOURCE = "Auto (Dhan → Yahoo)"
DEFAULT_DHAN_SECONDS = 300  # 5 minutes between full cash LTP snapshots
DHAN_LIMIT_BACKOFF_SECONDS = 1800  # 30 minutes after an HTTP 429


def choose_cash_source(choice: str, *, verified: bool, token_available: bool) -> str:
    if choice == AUTO_SOURCE:
        return "Dhan" if verified and token_available else "Yahoo Finance"
    if choice in ("Dhan", "Yahoo Finance"):
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
                       manual: bool = False) -> bool:
    if last_attempt is None:
        return True
    if now < last_attempt:
        return False
    window = retry_window_seconds(errors)
    # User refresh is allowed, but NOT when broker returned rate limit.
    if manual and window != DHAN_LIMIT_BACKOFF_SECONDS:
        return now - last_attempt >= 5
    return now - last_attempt >= window
