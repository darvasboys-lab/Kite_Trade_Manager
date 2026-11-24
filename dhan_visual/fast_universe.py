"""Fast, durable, read-only NSE stock F&O universe for Dhan Payoff Studio.

Primary: small NSE official per-expiry lot table + Dhan's NSE_EQ segment master.
Fallback: Dhan's NSE_EQ + NSE_FNO *segment* masters (not the giant global master).
Validated public symbol / expiry / lot / cash security ID rows alone are cached
as JSON. This cache never contains broker credentials or price data.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .dhan_market import (
    DhanDataError, DhanUnderlying, _cell, _expiry_month, _positive_integer,
)
from .screener import NSE_LOTS_URL, Underlying, SYMBOL_PATTERN, parse_nse_lots

EQ_SEGMENT_URL = "https://api.dhan.co/v2/instrument/NSE_EQ"
FNO_SEGMENT_URL = "https://api.dhan.co/v2/instrument/NSE_FNO"
CACHE_FILE = Path(__file__).resolve().parent.parent / ".dhan_cache" / "stock_fno_universe_v2.json"
MAX_CACHE_AGE_SECONDS = 20 * 3600
LAST_RESORT_AGE_SECONDS = 48 * 3600


def _csv_rows(payload: bytes):
    if not isinstance(payload, bytes) or not payload or b"\x00" in payload:
        raise DhanDataError("Invalid CSV bytes from market-data source")
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = payload.decode("cp1252")
    reader = csv.DictReader(io.StringIO(decoded), skipinitialspace=True)
    if not reader.fieldnames:
        raise DhanDataError("Instrument CSV has no header")
    names = [s.strip().upper().lstrip("\ufeff") for s in reader.fieldnames]
    if len(set(names)) != len(names):
        raise DhanDataError("Duplicate instrument header fields")
    reader.fieldnames = names
    return reader


def _download_csv(url: str, cap_bytes: int) -> bytes:
    import requests

    try:
        resp = requests.get(url, timeout=(6, 25),
                            headers={"Accept": "text/csv,*/*", "User-Agent": "DhanPayoffStudio/2.0"},
                            stream=True)
        resp.raise_for_status()
        if resp.url != url:
            raise DhanDataError("Unexpected source redirect")
        size = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > cap_bytes:
                raise DhanDataError("Instrument response exceeded safe maximum size")
            chunks.append(chunk)
        if not chunks:
            raise DhanDataError("Empty instrument file")
        return b"".join(chunks)
    except requests.RequestException as exc:
        raise DhanDataError(f"Instrument source unavailable ({type(exc).__name__})") from exc
    finally:
        if "resp" in locals():
            resp.close()


def parse_dhan_equities(payload: bytes) -> dict[str, int]:
    """Read only NSE_EQ ordinary equity instruments from a segment CSV."""
    equities = {}
    conflicted = set()
    for row in _csv_rows(payload):
        if _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper() != "NSE":
            continue
        if _cell(row, "SEGMENT", "SEM_SEGMENT").upper() not in ("E", "EQ", "NSE_EQ"):
            continue
        series = _cell(row, "SERIES", "SEM_SERIES").upper()
        if series and series not in ("EQ", "EQUITY"):
            continue
        sym = _cell(row, "TRADING_SYMBOL", "SEM_TRADING_SYMBOL", "SYMBOL_NAME", "SM_SYMBOL_NAME").upper()
        secid = _positive_integer(_cell(row, "SECURITY_ID", "SEM_SMST_SECURITY_ID"))
        if not secid or not SYMBOL_PATTERN.fullmatch(sym):
            continue
        if sym in equities and equities[sym] != secid:
            conflicted.add(sym)
        else:
            equities[sym] = secid
    for sym in conflicted:
        equities.pop(sym, None)
    if not equities:
        raise DhanDataError("No verified NSE_EQ equity symbols/security IDs found")
    return equities


def map_nse_lots_to_cash_security_ids(lot_payload: bytes, equities: dict[str, int]) -> list[DhanUnderlying]:
    """Prefer official NSE lots without scanning hundreds of thousands of contracts."""
    lot_rows = parse_nse_lots(lot_payload)
    return [
        DhanUnderlying(row, equities[row.symbol]) for row in lot_rows
        if row.symbol in equities
    ]


def parse_dhan_fno_segment(payload: bytes, equities: dict[str, int]) -> list[DhanUnderlying]:
    """Fallback if NSE lot file cannot be retrieved; scan derivatives only."""
    by_month = {}
    conflicts = set()
    for row in _csv_rows(payload):
        if _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper() != "NSE":
            continue
        if _cell(row, "SEGMENT", "SEM_SEGMENT").upper() not in ("D", "FNO", "NSE_FNO"):
            continue
        instrument = (_cell(row, "INSTRUMENT", "SEM_INSTRUMENT_NAME") + " "
                      + _cell(row, "INSTRUMENT_TYPE", "SEM_EXCH_INSTRUMENT_TYPE")).upper()
        if not ("STK" in instrument and ("OPT" in instrument or "FUT" in instrument)):
            continue
        symbol = _cell(row, "UNDERLYING_SYMBOL").upper()
        if symbol not in equities:
            continue
        under_id_raw = _cell(row, "UNDERLYING_SECURITY_ID")
        if under_id_raw:
            under_id = _positive_integer(under_id_raw)
            if under_id != equities[symbol]:
                continue
        expiry = _expiry_month(_cell(row, "SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"))
        lot = _positive_integer(_cell(row, "LOT_SIZE", "SEM_LOT_UNITS"))
        if not expiry or not lot:
            continue
        key = (symbol, expiry)
        if key in by_month and by_month[key] != lot:
            conflicts.add(key)
        else:
            by_month[key] = lot
    grouped = {}
    for (symbol, expiry), lot in by_month.items():
        if (symbol, expiry) in conflicts:
            continue
        grouped.setdefault(symbol, {})[expiry] = lot
    return [
        DhanUnderlying(Underlying(symbol, symbol, lots), equities[symbol])
        for symbol, lots in sorted(grouped.items()) if lots
    ]


def _validate_catalogue(catalogue: list[DhanUnderlying], minimum: int = 100):
    if len(catalogue) < minimum:
        raise DhanDataError(
            f"Incomplete stock F&O universe: {len(catalogue)} verified underlyings"
        )
    seen_symbols, seen_ids = set(), set()
    for record in catalogue:
        item = record.entry
        if (not SYMBOL_PATTERN.fullmatch(item.symbol)
                or item.symbol in seen_symbols or record.equity_security_id in seen_ids
                or not item.lots or any(type(v) is not int or v <= 0 for v in item.lots.values())):
            raise DhanDataError("Invalid/conflicting symbol, ID or lot in F&O list")
        seen_symbols.add(item.symbol)
        seen_ids.add(record.equity_security_id)
    return catalogue


def _encode_catalogue(catalogue, fetched_at: str, method: str):
    return {
        "schema_version": 2,
        "fetched_at": fetched_at,
        "method": method,
        "stocks": [
            {"symbol": x.entry.symbol, "id": x.equity_security_id,
             "lots": dict(x.entry.lots)}
            for x in catalogue
        ],
    }


def _read_cache(path: Path = CACHE_FILE):
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > 1_500_000:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 2:
            return None
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if fetched.tzinfo is None:
            return None
        now = datetime.now(timezone.utc)
        age = (now - fetched).total_seconds()
        if age < 0 or age > LAST_RESORT_AGE_SECONDS:
            return None
        data = [
            DhanUnderlying(Underlying(row["symbol"], row["symbol"], row["lots"]), row["id"])
            for row in payload["stocks"]
        ]
        _validate_catalogue(data)
        return data, age, fetched.isoformat(), payload.get("method", "cache")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, DhanDataError):
        return None


def _write_cache(catalogue, method: str, path: Path = CACHE_FILE):
    fetched_at = datetime.now(timezone.utc).isoformat()
    body = _encode_catalogue(catalogue, fetched_at, method)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".universe-", suffix=".tmp", delete=False) as tmp:
            tmp.write(json.dumps(body, separators=(",", ":"), ensure_ascii=True))
            name = tmp.name
        os.replace(name, path)
    except OSError:
        # Cache failure must not erase valid in-memory results.
        pass
    return fetched_at


def load_fast_catalogue(refresh: bool = False, progress=None, cache_path: Path = CACHE_FILE):
    """Return (mapped underlyings, visible source label).

    Prefer a fresh, validated, credential-free local cache. Otherwise fetch
    NSE_EQ + compact NSE official lot file. Fall back to Dhan NSE_FNO segment
    only when NSE lot file is unavailable. On transient source errors, a
    <=48-hour-old cache is explicitly flagged stale, never silently fresh.
    """
    def report(label):
        if progress is not None:
            progress(label)

    cached = _read_cache(cache_path)
    if cached and cached[1] <= MAX_CACHE_AGE_SECONDS and not refresh:
        data, age, stamp, method = cached
        report(f"Loaded {len(data)} stocks from local cache")
        return data, f"Local cache ({age/3600:.1f} hours old; {method})"
    started = time.monotonic()
    try:
        report("1/3 · Downloading Dhan NSE cash-equity security IDs...")
        equity_ids = parse_dhan_equities(_download_csv(EQ_SEGMENT_URL, 12_000_000))
        report(f"2/3 · Matched {len(equity_ids):,} NSE equity IDs; downloading stock F&O lots...")
        try:
            payload = _download_csv(NSE_LOTS_URL, 3_000_000)
            catalogue = map_nse_lots_to_cash_security_ids(payload, equity_ids)
            _validate_catalogue(catalogue)
            method = "NSE official F&O lots + Dhan NSE_EQ IDs"
        except (DhanDataError, ValueError, UnicodeError):
            report("2/3 · NSE lots unavailable/incomplete; trying segment-only Dhan NSE_FNO...")
            catalogue = parse_dhan_fno_segment(
                _download_csv(FNO_SEGMENT_URL, 80_000_000), equity_ids
            )
            _validate_catalogue(catalogue)
            method = "Dhan NSE_EQ + NSE_FNO segment masters"
        report("3/3 · Writing small, public-metadata-only local cache...")
        _write_cache(catalogue, method, cache_path)
        return catalogue, f"{method} · loaded in {time.monotonic()-started:.1f} seconds"
    except (DhanDataError, ValueError) as primary_error:
        # Dhan's segment endpoints can be blocked even when the documented
        # public detailed CSV is accessible. Try that automatic fallback before
        # ever asking a user to do anything by hand.
        try:
            report("Automatic fallback · downloading Dhan's public detailed master once...")
            from .dhan_market import fetch_dhan_master
            catalogue, _ = fetch_dhan_master()
            _validate_catalogue(catalogue)
            method = "Dhan public detailed master (automatic fallback)"
            _write_cache(catalogue, method, cache_path)
            return catalogue, f"{method} · loaded in {time.monotonic()-started:.1f} seconds"
        except (DhanDataError, ValueError) as backup_error:
            if cached:
                data, age, stamp, method = cached
                report("Automatic sources unavailable; existing cache is labelled STALE")
                return data, f"STALE CACHE ({age/3600:.1f}h; {method}). Refresh recommended."
            raise DhanDataError(
                "All automatic instrument sources were unavailable or invalid. "
                f"Segment/NSE: {type(primary_error).__name__}; "
                f"public detailed master: {type(backup_error).__name__}. "
                "Use Retry after checking your internet connection. No file upload is needed."
            ) from backup_error
