"""Complete stock F&O ATM straddle scanner — read-only Dhan market data.

User starts ONE background scan; it then visits *every* stock in the selected
expiry month under Dhan's published per-option-chain rate limit. Unknown or
invalid premiums are not ranked. Workers never access Streamlit state or UI.

The optional public NSE_FNO instrument master supplies VERIFIED exact expiry
dates to avoid 219 additional expirylist requests. On failure, each individual
expiry is verified by Dhan's optionchain/expirylist endpoint instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from queue import SimpleQueue
from threading import Event

from .atm_options import (
    AtmOptionQuote, OptionDataError, IST, _month_number, load_atm_options,
)
from .auto_auth import AutoAuthError, load_or_generate
from .dhan_market import DhanDataError, DhanUnderlying, _cell
from .fast_universe import (
    FNO_SEGMENT_URL, _csv_rows, _download_csv, resolve_equity_ids,
)


@dataclass(frozen=True)
class StraddleRow:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_ltp: Decimal
    call_ltp: Decimal
    put_ltp: Decimal
    combined_ltp: Decimal
    premium_pct: Decimal
    lot_size: int
    gross_lot_premium: Decimal
    call_bid: Decimal | None
    put_bid: Decimal | None
    combined_bid_pct: Decimal | None
    captured_at: str


@dataclass(frozen=True)
class OtmCall5Quote:
    """First listed CE strike >= Dhan option-chain spot * 1.05."""
    symbol: str
    expiry: str
    strike: Decimal
    underlying_ltp: Decimal
    call_ltp: Decimal
    premium_pct: Decimal
    captured_at: str


def otm_call_5_from_atm(value: AtmOptionQuote) -> OtmCall5Quote | None:
    """No estimate when no actual strike or no positive CE market LTP."""
    leg = value.otm_5_ce
    strike = value.otm_5_strike
    if (strike is None or leg is None or leg.ltp is None
            or value.underlying_price <= 0
            or strike < value.underlying_price * Decimal("1.05")
            or leg.ltp <= 0):
        return None
    return OtmCall5Quote(
        value.symbol, value.expiry, strike, value.underlying_price,
        leg.ltp, leg.ltp / value.underlying_price * Decimal(100),
        value.captured_at,
    )


@dataclass(frozen=True)
class ScanUpdate:
    symbol: str
    completed: int
    total: int
    row: StraddleRow | None = None
    error: str = ""
    fatal: bool = False
    status: str = ""
    otm_call: OtmCall5Quote | None = None


def straddle_from_atm(value: AtmOptionQuote) -> StraddleRow:
    """ATM straddle % = (CE LTP + PE LTP) / underlying LTP * 100.

    Compare the same exact expiry and ATM strike. The computed percentage
    is a gross LTP-based premium ratio, not realized profit, margin return,
    or necessarily executable sell credit.
    """
    spot, ce, pe = value.underlying_price, value.ce.ltp, value.pe.ltp
    if (spot is None or spot <= 0 or ce is None or ce <= 0
            or pe is None or pe <= 0):
        raise OptionDataError("Both ATM CE and PE LTPs and cash CMP are required")
    if value.lot_size <= 0:
        raise OptionDataError("A valid F&O lot is required")
    combined = ce + pe
    bids = (value.ce.bid, value.pe.bid)
    sell_bid_pct = (
        (sum(bids) / spot * 100) if all(x is not None and x > 0 for x in bids)
        else None
    )
    return StraddleRow(
        value.symbol, value.expiry, value.strike, spot, ce, pe, combined,
        combined / spot * Decimal(100), value.lot_size, combined * value.lot_size,
        value.ce.bid, value.pe.bid, sell_bid_pct, value.captured_at,
    )


def rank_straddles(rows: dict[str, StraddleRow] | list[StraddleRow]) -> list[StraddleRow]:
    values = rows.values() if isinstance(rows, dict) else rows
    return sorted(values, key=lambda x: (-x.premium_pct, x.symbol))


def _parse_expiry(raw: str) -> date | None:
    text = str(raw or "").strip()
    for value in (text[:10], text):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    for pattern in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def expiry_map_from_dhan_fno(payload: bytes, symbols: set[str],
                             month: str, today: date | None = None) -> dict[str, str]:
    """Extract actual active OPTSTK expiries, never derive one from a calendar rule."""
    year, mon = _month_number(month)
    today = today or datetime.now(IST).date()
    candidates: dict[str, set[date]] = {}
    for row in _csv_rows(payload):
        if _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper() != "NSE":
            continue
        if _cell(row, "SEGMENT", "SEM_SEGMENT").upper() not in ("D", "FNO", "NSE_FNO"):
            continue
        instrument = (
            _cell(row, "INSTRUMENT", "SEM_INSTRUMENT_NAME") + " "
            + _cell(row, "INSTRUMENT_TYPE", "SEM_EXCH_INSTRUMENT_TYPE")
        ).upper()
        if "OPTSTK" not in instrument and "STKOPT" not in instrument:
            continue
        symbol = _cell(row, "UNDERLYING_SYMBOL", "SM_SYMBOL_NAME").upper()
        if symbol not in symbols:
            continue
        expiry = _parse_expiry(_cell(row, "SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"))
        if not expiry or expiry < today or (expiry.year, expiry.month) != (year, mon):
            continue
        candidates.setdefault(symbol, set()).add(expiry)
    return {sym: min(dates).isoformat() for sym, dates in candidates.items() if dates}


def _verified_expiry_map(symbols: set[str], month: str) -> dict[str, str]:
    # This public, unauthenticated CSV is an optimization only. If blocked,
    # we use the individual Dhan expirylist for each stock.
    payload = _download_csv(FNO_SEGMENT_URL, 80_000_000, timeout=(5, 23))
    return expiry_map_from_dhan_fno(payload, symbols, month)


def scan_all_straddles(
    underlyings: list[DhanUnderlying], month: str, client_id: str, token: str,
    updates: SimpleQueue, cancel: Event,
    *,
    existing: set[str] | None = None,
    auto_login: bool = False,
    master_lookup=True,
    priority_symbols: list[str] | None = None,
) -> tuple[int, int]:
    """A complete, cancellable, resumable, rate-limited scan of ALL stocks.

    Emits one ScanUpdate per scanned symbol. No silent first-50 cap, no
    per-stock user clicks, no retries on 401/403/429. A broker/API failure
    does not cause an unbounded loop or fabricate premium values.
    """
    universe = {x.entry.symbol: x for x in underlyings if month in x.entry.lots}
    # Ranking only affects the request order. New, unranked, or below-EMA200
    # stocks remain included; a changed universe is never silently omitted.
    front = list(dict.fromkeys(
        sym for sym in (priority_symbols or ()) if sym in universe
    ))
    symbols = front + sorted(set(universe).difference(front))
    total = len(symbols)
    done = set(existing or set()).intersection(symbols)
    completed = len(done)
    if not symbols:
        return 0, 0
    if cancel.is_set():
        return completed, total
    if not token and auto_login:
        updates.put(ScanUpdate("", completed, total, status="Authenticating with Dhan…"))
        try:
            access = load_or_generate(client_id)
            token = access.token
        except AutoAuthError as exc:
            updates.put(ScanUpdate("", completed, total, error=f"Auto-login: {exc}", fatal=True))
            return completed, total
    if len(token) < 16:
        updates.put(ScanUpdate("", completed, total,
                               error="A valid Dhan market-data access token is required", fatal=True))
        return completed, total

    if any(item.equity_security_id <= 0 for item in universe.values()):
        updates.put(ScanUpdate("", completed, total, status="Resolving cash-stock security IDs…"))
        try:
            verified = resolve_equity_ids(set(symbols))
        except DhanDataError as exc:
            updates.put(ScanUpdate("", completed, total,
                                   error=f"Could not resolve NSE cash IDs: {exc}", fatal=True))
            return completed, total
        universe = {
            sym: DhanUnderlying(item.entry, verified.get(sym, item.equity_security_id))
            for sym, item in universe.items()
        }

    expiry_map = {}
    if master_lookup and not cancel.is_set():
        updates.put(ScanUpdate("", completed, total, status="Checking exact NSE stock-option expiries…"))
        try:
            expiry_map = _verified_expiry_map(set(symbols), month)
        except (DhanDataError, ValueError, UnicodeError):
            # The public source may be blocked; Dhan's authenticated expirylist
            # remains the exact, safe fallback for each requested stock.
            updates.put(ScanUpdate("", completed, total, status="Using Dhan expiry lookup per stock…"))

    for sym in symbols:
        if cancel.is_set():
            break
        if sym in done:
            continue
        item = universe[sym]
        completed += 1
        if item.equity_security_id <= 0:
            updates.put(ScanUpdate(sym, completed, total, error="NSE_EQ security ID unavailable"))
            continue
        try:
            atm = load_atm_options(
                sym, month, item.entry.lots[month], item.equity_security_id,
                client_id, token, verified_expiry=expiry_map.get(sym),
            )
            # One Dhan option-chain response supplies ATM straddle AND +5% CE.
            # Even when ATM PE is absent, preserve an independently valid CE.
            otm_call = otm_call_5_from_atm(atm)
            try:
                row = straddle_from_atm(atm)
            except OptionDataError as exc:
                updates.put(ScanUpdate(
                    sym, completed, total, error=str(exc)[:250], otm_call=otm_call,
                ))
            else:
                updates.put(ScanUpdate(
                    sym, completed, total, row=row, otm_call=otm_call,
                ))
        except (OptionDataError, DhanDataError) as exc:
            safe = str(exc)[:250]
            fatal = any(
                flag in safe.lower()
                for flag in ("http 401", "http 403", "http 429", "permission",
                             "not authorized", "invalid token")
            )
            updates.put(ScanUpdate(sym, completed, total, error=safe, fatal=fatal))
            if fatal:
                break
        except Exception as exc:
            updates.put(ScanUpdate(sym, completed, total,
                                   error=f"Unexpected market-data error ({type(exc).__name__})"))
    return completed, total
