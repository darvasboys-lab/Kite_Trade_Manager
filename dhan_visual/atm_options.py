"""On-demand, read-only ATM stock option chain from DhanHQ v2.

No request is sent when Search Stock renders. Only a user click explicitly
requests the selected symbol's exact expiry list and option chain. All network
calls are authenticated market-data POSTs; no orders or account mutations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from threading import Lock
from time import monotonic, sleep
from typing import Any
import re

import requests

from .dhan_market import DhanDataError
from .fast_universe import resolve_equity_ids

EXPIRIES_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
IST = timezone(timedelta(hours=5, minutes=30))
MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MAX_CHAIN_STRIKES = 3000


class OptionDataError(DhanDataError):
    """Safe-to-display read-only option-data failure."""


@dataclass(frozen=True)
class OptionLeg:
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None

    def premium_per_lot(self, lot_size: int) -> Decimal | None:
        return (self.ltp * lot_size).quantize(Decimal("0.01")) if self.ltp is not None else None


@dataclass(frozen=True)
class AtmOptionQuote:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_price: Decimal
    lot_size: int
    ce: OptionLeg
    pe: OptionLeg
    captured_at: str


class _RateLimiter:
    """Respect Dhan's one unique option-chain request per 3 seconds."""
    def __init__(self):
        self.lock = Lock()
        self.next_at = 0.0

    def acquire(self):
        with self.lock:
            delay = max(0.0, self.next_at - monotonic())
            if delay:
                sleep(delay)
            self.next_at = monotonic() + 3.15


RATE_LIMITER = _RateLimiter()


def _number(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
        if value.is_finite() and value > 0:
            return value
    except (TypeError, ValueError, InvalidOperation):
        pass
    return None


def _month_number(expiry_month: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]{3})-(\d{2})", expiry_month)
    if not match or match.group(1) not in MONTH_ABBR:
        raise OptionDataError("Invalid F&O month selected")
    return 2000 + int(match.group(2)), MONTH_ABBR.index(match.group(1)) + 1


def nearest_active_expiry(payload: dict, expiry_month: str,
                          today: date | None = None) -> str:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise OptionDataError("Dhan did not return a valid option expiry list")
    raw = payload.get("data")
    if not isinstance(raw, list) or len(raw) > 200:
        raise OptionDataError("Dhan expiry list is malformed")
    year, month = _month_number(expiry_month)
    today = today or datetime.now(IST).date()
    eligible = []
    for item in raw:
        try:
            value = date.fromisoformat(item)
        except (ValueError, TypeError):
            continue
        if value.year == year and value.month == month and value >= today:
            eligible.append(value)
    if not eligible:
        raise OptionDataError(
            f"No active Dhan option expiry found for {expiry_month}; "
            "choose an available F&O month."
        )
    return min(eligible).isoformat()


def parse_atm_option_chain(payload: dict, symbol: str, expiry: str,
                           lot_size: int, stamp: str) -> AtmOptionQuote:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise OptionDataError("Dhan option-chain request was not successful")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise OptionDataError("Dhan option-chain data is missing")
    spot = _number(data.get("last_price"))
    if spot is None:
        raise OptionDataError("Dhan option chain has no usable underlying price for ATM selection")
    raw_strikes = data.get("oc")
    if not isinstance(raw_strikes, dict) or not 1 <= len(raw_strikes) <= MAX_CHAIN_STRIKES:
        raise OptionDataError("Dhan option chain contains no valid strikes")
    strikes: list[tuple[Decimal, dict]] = []
    for label, legs in raw_strikes.items():
        strike = _number(label)
        if strike is None or not isinstance(legs, dict):
            continue
        if not any(isinstance(legs.get(k), dict) for k in ("ce", "pe")):
            continue
        strikes.append((strike, legs))
    if not strikes:
        raise OptionDataError("Dhan option chain has no usable CE or PE strike records")
    strike, raw_legs = min(strikes, key=lambda x: (abs(x[0] - spot), x[0]))

    def read_leg(name: str) -> OptionLeg:
        record = raw_legs.get(name)
        if not isinstance(record, dict):
            return OptionLeg(None, None, None)
        return OptionLeg(
            _number(record.get("last_price")),
            _number(record.get("top_bid_price")),
            _number(record.get("top_ask_price")),
        )

    if not isinstance(lot_size, int) or lot_size <= 0:
        raise OptionDataError("Invalid stock-option market lot size")
    return AtmOptionQuote(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        underlying_price=spot,
        lot_size=lot_size,
        ce=read_leg("ce"),
        pe=read_leg("pe"),
        captured_at=stamp,
    )


def _read_json(client_id: str, token: str, url: str, payload: dict,
               session=None) -> dict:
    if url not in (EXPIRIES_URL, OPTION_CHAIN_URL):
        raise OptionDataError("Unsupported market-data endpoint")
    if not re.fullmatch(r"[0-9]{8,15}", client_id.strip()) or len(token.strip()) < 16:
        raise OptionDataError("A valid local Dhan Client ID and access token are required")
    session = session or requests
    RATE_LIMITER.acquire()
    try:
        response = session.post(
            url,
            json=payload,
            headers={"access-token": token.strip(), "client-id": client_id.strip(),
                     "Accept": "application/json", "Content-Type": "application/json"},
            timeout=(7, 20),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise OptionDataError(
                f"Dhan option data returned HTTP {response.status_code}; "
                "check local token and Data API entitlement"
            )
        result = response.json()
        if not isinstance(result, dict):
            raise OptionDataError("Dhan option-data response is not JSON object")
        return result
    except OptionDataError:
        raise
    except requests.RequestException as exc:
        raise OptionDataError(f"Dhan option data network failure ({type(exc).__name__})") from exc
    except ValueError as exc:
        raise OptionDataError("Dhan returned invalid JSON option data") from exc


def load_atm_options(symbol: str, expiry_month: str, lot_size: int,
                     cash_security_id: int, client_id: str, token: str,
                     session=None) -> AtmOptionQuote:
    """User-initiated, two authenticated read-only requests for ONE stock."""
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol):
        raise OptionDataError("Invalid NSE F&O stock symbol")
    secid = cash_security_id if isinstance(cash_security_id, int) and cash_security_id > 0 else None
    if secid is None:
        try:
            ids = resolve_equity_ids({symbol})
            secid = ids.get(symbol)
        except DhanDataError as exc:
            raise OptionDataError(f"Could not verify NSE_EQ ID for {symbol}: {exc}") from exc
    if not secid:
        raise OptionDataError("Dhan NSE cash security ID is unavailable for this stock")
    instrument = {"UnderlyingScrip": secid, "UnderlyingSeg": "NSE_EQ"}
    expiry_response = _read_json(client_id, token, EXPIRIES_URL, instrument, session=session)
    expiry = nearest_active_expiry(expiry_response, expiry_month)
    chain_response = _read_json(
        client_id, token, OPTION_CHAIN_URL, {**instrument, "Expiry": expiry}, session=session
    )
    return parse_atm_option_chain(
        chain_response, symbol, expiry, lot_size,
        datetime.now(IST).strftime("%d %b %Y %H:%M:%S IST"),
    )
