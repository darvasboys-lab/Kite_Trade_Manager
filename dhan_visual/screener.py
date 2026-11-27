"""Read-only NSE stock F&O catalogue and indicative Yahoo quote screen calculations.

NSE is authoritative for expiry-specific permitted lot sizes.
Yahoo bar closes are indicative and may be delayed/stale/missing; never used to trade.
No broker accounts, order imports, credentials or background workers.
"""
from __future__ import annotations

from .api_monitor import record

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

NSE_LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
INDEX_SYMBOLS = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "NIFTYFPI", "NIFTYINDIAFPI150", "SENSEX", "BANKEX",
})
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTH_PATTERN = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[- ]?(\d{2}|\d{4})$")
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,39}$")


@dataclass(frozen=True)
class Underlying:
    symbol: str
    name: str
    lots: Mapping[str, int]


@dataclass(frozen=True)
class Quote:
    price: Decimal
    timestamp: str
    source: str


def normalize_expiry(label: str) -> str | None:
    compact = re.sub(r"\s+", "", label.strip().upper())
    m = MONTH_PATTERN.fullmatch(compact)
    if not m:
        return None
    year = int(m.group(2))
    if year < 100:
        year += 2000
    if not 2020 <= year <= 2100:
        return None
    return f"{m.group(1)}-{year % 100:02d}"


def expiry_sort_key(label: str) -> tuple[int, int]:
    month, year = label.split("-")
    return int("20" + year), MONTHS.index(month) + 1


def parse_nse_lots(data: str | bytes) -> list[Underlying]:
    """Parse NSE 'Permitted lot size' CSV, preserving distinct expiry-month lots.

    Rejects unsupported data, index derivatives, duplicate symbols, and
    blank/zero/non-integer market-lot values. Missing lots stay missing.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            data = data.decode("cp1252")
    if not isinstance(data, str) or not data.strip() or "\x00" in data:
        raise ValueError("Invalid NSE lot-size file")
    rows = list(csv.reader(io.StringIO(data.lstrip("\ufeff")), skipinitialspace=True))
    header_idx = None
    symbol_idx = None
    name_idx = None
    expiry_cols = {}
    for i, row in enumerate(rows[:30]):
        names = [re.sub(r"\s+", " ", c.strip().upper().replace("\ufeff", "")) for c in row]
        sym_positions = [n for n, label in enumerate(names) if label in ("SYMBOL", "NSE SYMBOL")]
        months = {j: normalize_expiry(label) for j, label in enumerate(names) if normalize_expiry(label)}
        if sym_positions and months:
            header_idx = i
            symbol_idx = sym_positions[0]
            expiry_cols = months
            name_idx = next((j for j, name in enumerate(names)
                             if name in ("UNDERLYING", "COMPANY NAME", "NAME OF UNDERLYING")), None)
            break
    if header_idx is None:
        raise ValueError("NSE CSV is missing SYMBOL and expiry-month lot columns")
    known = {}
    for row in rows[header_idx + 1:]:
        if len(row) <= symbol_idx:
            continue
        symbol = row[symbol_idx].strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol) or symbol in INDEX_SYMBOLS:
            continue
        if symbol in known:
            raise ValueError(f"NSE lot CSV repeats underlying symbol {symbol}")
        lots = {}
        for col, expiry in expiry_cols.items():
            cell = row[col].strip().replace(",", "") if col < len(row) else ""
            if not cell or cell in ("-", "NA", "N/A", "0"):
                continue
            try:
                value = Decimal(cell)
            except InvalidOperation:
                continue
            if value.is_finite() and value > 0 and value == int(value):
                lots[expiry] = int(value)
        if lots:
            name = row[name_idx].strip() if name_idx is not None and name_idx < len(row) else symbol
            known[symbol] = Underlying(symbol, name or symbol, lots)
    if not known:
        raise ValueError("No eligible stock lot sizes found in NSE CSV")
    return sorted(known.values(), key=lambda x: x.symbol)


def expiry_options(rows: list[Underlying]) -> list[str]:
    return sorted({month for row in rows for month in row.lots}, key=expiry_sort_key)


def choose_expiry(rows: list[Underlying], now: datetime | None = None) -> str:
    """Prefer the first expiry not older than the current calendar month."""
    now = now or datetime.now(timezone.utc)
    opts = expiry_options(rows)
    if not opts:
        raise ValueError("No expiry months in source")
    current = (now.year, now.month)
    return next((x for x in opts if expiry_sort_key(x) >= current), opts[-1])


def fetch_nse_catalogue() -> tuple[list[Underlying], str]:
    """Read NSE's primary official lot-size CSV; fail closed, never invent lots."""
    import requests

    resp = requests.get(
        NSE_LOTS_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CoveredCallScreener/1.0)",
                 "Accept": "text/csv,text/plain,*/*",
                 "Referer": "https://www.nseindia.com/"},
        timeout=(7, 24),
    )
    resp.raise_for_status()
    if not resp.url.startswith("https://nsearchives.nseindia.com/"):
        raise ValueError("Unexpected NSE lot-size download redirect")
    if len(resp.content) > 2_000_000:
        raise ValueError("Unexpectedly large NSE lot-size response")
    rows = parse_nse_lots(resp.content)
    if len(rows) < 100:
        raise ValueError("Suspiciously incomplete NSE stock F&O catalogue (fewer than 100)")
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return rows, fetched_at


def as_yahoo_symbol(nse_symbol: str) -> str:
    if not SYMBOL_PATTERN.fullmatch(nse_symbol):
        raise ValueError("Invalid NSE equity symbol")
    return nse_symbol + ".NS"


def extract_latest_closes(frame, tickers: list[str], interval: str) -> dict[str, Quote]:
    """Read Yahoo's normal or transposed MultiIndex download layout.

    Never substitute a quote belonging to another ticker for a missing one.
    """
    import pandas as pd

    if frame is None or frame.empty:
        return {}
    out = {}
    for symbol in tickers:
        ticker = as_yahoo_symbol(symbol)
        close = None
        columns = frame.columns
        if isinstance(columns, pd.MultiIndex):
            if (ticker, "Close") in columns:
                close = frame[(ticker, "Close")]
            elif ("Close", ticker) in columns:
                close = frame[("Close", ticker)]
        elif len(tickers) == 1 and "Close" in columns:
            close = frame["Close"]
        if close is None:
            continue
        cleaned = close.dropna()
        if cleaned.empty:
            continue
        value = cleaned.iloc[-1]
        try:
            price = Decimal(str(float(value)))
        except (ValueError, InvalidOperation, TypeError, OverflowError):
            continue
        if not price.is_finite() or price <= 0:
            continue
        timestamp = cleaned.index[-1]
        stamp = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        out[symbol] = Quote(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                            stamp, f"Yahoo {interval} bar close")
    return out


def fetch_yahoo_quotes(symbols: list[str], batch_size: int = 25) -> tuple[dict[str, Quote], list[str]]:
    """Free indicative Yahoo bars; batch to reduce traffic, no fabricated fallbacks."""
    import yfinance as yf

    quotes, errors = {}, []
    for offset in range(0, len(symbols), batch_size):
        group = symbols[offset:offset + batch_size]
        tickers = " ".join(as_yahoo_symbol(s) for s in group)
        try:
            record("Yahoo")
            frame = yf.download(
                tickers=tickers, interval="15m", period="5d", group_by="ticker",
                auto_adjust=False, progress=False, threads=True, timeout=12,
            )
            fresh = extract_latest_closes(frame, group, "15m")
            quotes.update(fresh)
        except Exception as exc:
            errors.append(f"15-minute quote batch {offset // batch_size + 1}: {type(exc).__name__}")
        missing = [symbol for symbol in group if symbol not in quotes]
        if missing:
            try:
                record("Yahoo")
                daily = yf.download(
                    tickers=" ".join(as_yahoo_symbol(s) for s in missing),
                    interval="1d", period="5d", group_by="ticker",
                    auto_adjust=False, progress=False, threads=True, timeout=12,
                )
                quotes.update(extract_latest_closes(daily, missing, "daily"))
            except Exception as exc:
                errors.append(f"Daily quote batch {offset // batch_size + 1}: {type(exc).__name__}")
    return quotes, errors


def screen_rows(
    catalogue: list[Underlying], expiry: str, quotes: Mapping[str, Quote],
    contains: str = "", min_value: float = 0, max_value: float = 0,
    descending: bool = False,
) -> list[dict]:
    """Return numeric, sortable contract values with missing prices explicitly N/A."""
    lo, hi = Decimal(str(min_value)), Decimal(str(max_value))
    if lo < 0 or hi < 0 or (hi and hi < lo):
        raise ValueError("Maximum contract value must be at least the minimum")
    needle = contains.strip().upper()
    out = []
    for entry in catalogue:
        lot = entry.lots.get(expiry)
        if not lot or (needle and needle not in entry.symbol and needle not in entry.name.upper()):
            continue
        quote = quotes.get(entry.symbol)
        value = (quote.price * lot).quantize(Decimal("0.01")) if quote else None
        if value is None and (lo > 0 or hi > 0):
            continue  # Do not falsely include a no-quote row in numeric range screens.
        if value is not None and (value < lo or (hi and value > hi)):
            continue
        out.append({
            "Symbol": entry.symbol, "Company": entry.name,
            "Last price (₹)": float(quote.price) if quote else None,
            "Lot size": lot, "Contract value (₹)": float(value) if value is not None else None,
            "Quote time": quote.timestamp if quote else "Unavailable",
            "Price source": quote.source if quote else "No Dhan quote",
        })
    # Missing quotes are ALWAYS last, even when sorting descending.
    return sorted(out, key=lambda row: (
        row["Contract value (₹)"] is None,
        -row["Contract value (₹)"] if descending and row["Contract value (₹)"] is not None
        else row["Contract value (₹)"] if row["Contract value (₹)"] is not None else 0,
        row["Symbol"],
    ))
