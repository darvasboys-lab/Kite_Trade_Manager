"""Optional TejHQ Retail/Pro *EOD* stock-option CE reader, no extra packages.

Documented API: GET /v1/fo/chain/{symbol}?expiry=YYYY-MM-DD.
Never requests undocumented endpoints, trades, or free-tier gated resources.
User explicitly supplies a paid-tier key and starts a read-only scan.
All selected CE prices and underlying values share the same API report date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from queue import SimpleQueue
from threading import Event
from time import monotonic, sleep
import re

import requests


API_BASE = "https://api.tejhq.dev/v1/fo"
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,39}$")
MONTH_RE = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{2}$")
MAX_CHAIN_BYTES = 2 * 1024 * 1024
MIN_SECONDS_PER_CALL = 0.5
MAX_SYMBOLS_PER_SCAN = 250


class TejEodError(ValueError):
    """Provider unavailable, insufficient entitlement, or invalid data."""


@dataclass(frozen=True)
class TejEodCall:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_close: Decimal
    call_close: Decimal
    premium_pct: Decimal
    report_date: str
    volume: int


def _positive(value) -> Decimal | None:
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _expiry_matches(expiry: str, month: str) -> bool:
    try:
        dt = date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return False
    return dt.strftime("%b-%y").upper() == month


def parse_tej_chain(symbol: str, month: str, payload: dict) -> TejEodCall:
    """Select the *first listed* CE strike >= 105% of same-day underlying.

    Do not switch to a higher strike when the first one has an invalid close.
    Untraded closes are reference only and are labelled in the UI.
    """
    if not isinstance(payload, dict):
        raise TejEodError("Invalid TejHQ response")
    meta, data = payload.get("meta"), payload.get("data")
    if not isinstance(meta, dict) or not isinstance(data, list):
        raise TejEodError("TejHQ chain missing data or metadata")
    stamp = meta.get("date")
    try:
        date.fromisoformat(stamp)
    except (TypeError, ValueError):
        raise TejEodError("TejHQ chain missing valid report date") from None
    underlying = _positive(meta.get("underlying"))
    if underlying is None:
        raise TejEodError("TejHQ chain missing same-date underlying")
    candidates = []
    for row in data:
        if not isinstance(row, dict) or row.get("option_type") != "CE":
            continue
        instrument = (row.get("instrument") or "OPTSTK").upper()
        if instrument != "OPTSTK":
            continue
        expiry = row.get("expiry") or meta.get("expiry")
        if not _expiry_matches(expiry, month):
            continue
        strike = _positive(row.get("strike"))
        if strike is None or strike < underlying * Decimal("1.05"):
            continue
        candidates.append((strike, expiry, row))
    if not candidates:
        raise TejEodError("No listed stock CE at or above +5% for selected expiry")
    candidates.sort(key=lambda item: item[0])
    strike, expiry, row = candidates[0]
    if sum(x[0] == strike for x in candidates) != 1:
        raise TejEodError("Duplicate first-eligible CE strike")
    close = _positive(row.get("close"))
    if close is None:
        raise TejEodError("First +5% CE has no published close")
    raw_contracts = row.get("contracts", 0)
    try:
        quantity = int(raw_contracts)
        if quantity < 0 or Decimal(str(raw_contracts)) != quantity:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        quantity = 0
    return TejEodCall(
        symbol=symbol, expiry=expiry, strike=strike,
        underlying_close=underlying, call_close=close,
        premium_pct=close / underlying * Decimal(100),
        report_date=stamp, volume=quantity,
    )


def _get_json(url: str, token: str, *, get=None) -> dict:
    getter = get or requests.get
    try:
        with getter(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "CoveredCallStrategyBuilder/1.0",
            },
            timeout=(4, 12), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code in (401, 402, 403):
                raise TejEodError(
                    "TejHQ API key needs Retail/Pro F&O access (HTTP "
                    f"{response.status_code})"
                )
            if response.status_code == 429:
                raise TejEodError("TejHQ rate limit reached (HTTP 429)")
            if response.status_code != 200:
                raise TejEodError(f"TejHQ request failed (HTTP {response.status_code})")
            length = response.headers.get("content-length", "")
            if length.isdecimal() and int(length) > MAX_CHAIN_BYTES:
                raise TejEodError("TejHQ response exceeds data safety limit")
            raw = bytearray()
            for block in response.iter_content(chunk_size=65536):
                raw.extend(block)
                if len(raw) > MAX_CHAIN_BYTES:
                    raise TejEodError("TejHQ response exceeds data safety limit")
            import json
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TejEodError("TejHQ returned an invalid JSON envelope")
            return result
    except requests.RequestException:
        raise TejEodError("TejHQ connection failed; check provider access") from None
    except (ValueError, UnicodeDecodeError) as exc:
        if isinstance(exc, TejEodError):
            raise
        raise TejEodError("Invalid TejHQ JSON response") from None


def read_tej_chain(symbol: str, month: str, token: str, *, get=None) -> TejEodCall:
    """One documented option-chain call; no expiry guessing or fallback quote."""
    if not SYMBOL_RE.fullmatch(symbol) or not MONTH_RE.fullmatch(month):
        raise TejEodError("Invalid stock symbol or expiry month")
    if len(token) < 12 or any(ch in token for ch in "\r\n"):
        raise TejEodError("Enter a valid TejHQ Retail/Pro API key")
    # TejHQ returns the closest active expiry when expiry is omitted. Only
    # accept it if the response's actual expiry matches the selected month.
    # No broker market orders or changes to user holdings are possible here.
    payload = _get_json(f"{API_BASE}/chain/{symbol}", token, get=get)
    return parse_tej_chain(symbol, month, payload)


def scan_tej_eod(symbols, month: str, token: str, events: SimpleQueue,
                 cancel: Event, *, reader=read_tej_chain,
                 sleep_fn=sleep) -> None:
    """Finite, paced, user-started read-only scan; errors reported by symbol."""
    if len(symbols) > MAX_SYMBOLS_PER_SCAN:
        events.put(("fatal", f"Maximum {MAX_SYMBOLS_PER_SCAN} stocks per EOD scan"))
        return
    asof = None
    unique = set()
    for i, symbol in enumerate(symbols):
        if cancel.is_set():
            events.put(("done", "Stopped by user"))
            return
        if symbol in unique:
            continue
        unique.add(symbol)
        started = monotonic()
        try:
            quote = reader(symbol, month, token)
            if asof is None:
                asof = quote.report_date
            if quote.report_date != asof:
                raise TejEodError("Conflicting report dates; skipped to prevent mixed data")
            events.put(("quote", symbol, quote))
        except TejEodError as exc:
            events.put(("error", symbol, str(exc)))
            # Entitlement and rate limits affect the entire scan.
            if "Retail/Pro F&O access" in str(exc) or "rate limit" in str(exc):
                events.put(("fatal", str(exc)))
                return
        except Exception:
            events.put(("error", symbol, "Unexpected provider response"))
        elapsed = monotonic() - started
        if i < len(symbols) - 1 and not cancel.is_set():
            sleep_fn(max(0, MIN_SECONDS_PER_CALL - elapsed))
    events.put(("done", "Completed"))
