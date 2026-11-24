"""Read user-supplied official NSE F&O UDiFF daily bhavcopy for +5% OTM CE.

No network requests, brokers, orders, scraping, or synthetic option prices.
The close and underlying reference MUST come from the same dated NSE report.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
from zipfile import BadZipFile, ZipFile, is_zipfile


MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 250_000
REQUIRED = {
    "BizDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric",
    "OptnTp", "ClsPric", "UndrlygPric", "TtlTradgVol",
}


class NseEodError(ValueError):
    """Invalid or unusable public NSE daily file."""


@dataclass(frozen=True)
class NseEodCall:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_close: Decimal
    call_close: Decimal
    premium_pct: Decimal
    report_date: str
    volume: int


@dataclass(frozen=True)
class NseEodSnapshot:
    report_date: str
    quotes: dict[str, dict[str, NseEodCall]]


def _number(raw: str) -> Decimal | None:
    try:
        number = Decimal(str(raw).strip().replace(",", ""))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(raw: str) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _month(day: date) -> str:
    return day.strftime("%b-%y").upper()


def _load_csv_bytes(payload: bytes) -> bytes:
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise NseEodError("NSE daily file is empty or exceeds the 16 MB upload limit")
    if not is_zipfile(io.BytesIO(payload)):
        return payload
    try:
        with ZipFile(io.BytesIO(payload)) as zf:
            entries = [
                entry for entry in zf.infolist()
                if not entry.is_dir() and entry.filename.lower().endswith(".csv")
            ]
            if len(entries) != 1:
                raise NseEodError("NSE ZIP must contain exactly one CSV report")
            entry = entries[0]
            if entry.file_size <= 0 or entry.file_size > MAX_CSV_BYTES:
                raise NseEodError("NSE report size exceeds safety limit")
            with zf.open(entry) as handle:
                raw = handle.read(MAX_CSV_BYTES + 1)
            if len(raw) > MAX_CSV_BYTES:
                raise NseEodError("NSE report decompressed beyond safety limit")
            return raw
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise NseEodError("Invalid NSE daily ZIP file") from exc


def parse_nse_fo_eod(payload: bytes) -> NseEodSnapshot:
    """Parse one official daily report, retaining actual listed stock CE contracts.

    If multiple expiries exist within a month for one stock, omit that stock
    rather than silently select a contract that may not match the user's month.
    Non-trading first-eligible strikes are left unavailable, not substituted.
    """
    raw = _load_csv_bytes(payload)
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NseEodError("NSE CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(csv_text), skipinitialspace=True)
    if not reader.fieldnames or not REQUIRED.issubset(set(reader.fieldnames)):
        raise NseEodError("Not an NSE F&O UDiFF bhavcopy: required headers missing")

    asof = None
    groups: dict[tuple[str, str], dict[date, list[tuple[Decimal, Decimal, Decimal, int]]]] = {}
    seen_stock_ce = 0
    for row_index, row in enumerate(reader, start=1):
        if row_index > MAX_RECORDS:
            raise NseEodError("NSE daily report exceeds record safety limit")
        day = _date(row.get("BizDt", ""))
        if day is None:
            raise NseEodError("NSE report has an invalid business date")
        if asof is None:
            asof = day
        elif day != asof:
            raise NseEodError("NSE CSV contains multiple business dates")
        if (row.get("FinInstrmTp") or "").strip().upper() != "STO":
            continue
        if (row.get("OptnTp") or "").strip().upper() != "CE":
            continue
        seen_stock_ce += 1
        symbol = (row.get("TckrSymb") or "").strip().upper()
        expiry = _date(row.get("XpryDt", ""))
        strike = _number(row.get("StrkPric", ""))
        underlying = _number(row.get("UndrlygPric", ""))
        close = _number(row.get("ClsPric", ""))
        raw_volume = _number(row.get("TtlTradgVol", ""))
        if not symbol or not expiry or expiry < day or not strike or not underlying:
            continue
        # Volume must be an integer. A zero-volume close is not a traded quote.
        volume = int(raw_volume) if raw_volume is not None and raw_volume == int(raw_volume) else 0
        groups.setdefault((symbol, _month(expiry)), {}).setdefault(expiry, []).append(
            (strike, underlying, close or Decimal(0), volume)
        )

    if asof is None or seen_stock_ce == 0:
        raise NseEodError("No stock option CE contracts were found in the report")
    result: dict[str, dict[str, NseEodCall]] = {}
    for (symbol, month), expiry_map in groups.items():
        if len(expiry_map) != 1:
            continue  # Cannot resolve this stock/month's exact expiry safely.
        expiry, strikes = next(iter(expiry_map.items()))
        spots = {spot for _, spot, _, _ in strikes}
        if len(spots) != 1:
            continue  # Conflicting underlying reference values.
        spot = next(iter(spots))
        eligible = [(strike, close, volume) for strike, _, close, volume in strikes
                    if strike >= spot * Decimal("1.05")]
        if not eligible:
            continue
        first_strike = min(strike for strike, _, _ in eligible)
        at_strike = [(close, volume) for strike, close, volume in eligible if strike == first_strike]
        if len(at_strike) != 1:
            continue
        close, volume = at_strike[0]
        if volume <= 0 or close <= 0:
            continue
        quote = NseEodCall(
            symbol=symbol, expiry=expiry.isoformat(), strike=first_strike,
            underlying_close=spot, call_close=close,
            premium_pct=close / spot * Decimal(100),
            report_date=asof.isoformat(), volume=volume,
        )
        result.setdefault(month, {})[symbol] = quote
    return NseEodSnapshot(report_date=asof.isoformat(), quotes=result)
