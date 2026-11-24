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


def _download_csv(url: str, cap_bytes: int, timeout: tuple = (5, 14)) -> bytes:
    """Download only known public URLs. HTTPS redirects are normal for CSV/CDNs.

    No Dhan credential is attached to these public instrument-list GETs.
    Errors report the stage + HTTP status or exception type, never tokens.
    """
    import requests
    from urllib.parse import urlparse

    host = urlparse(url).hostname or "instrument source"
    try:
        resp = requests.get(
            url, timeout=timeout, allow_redirects=True, stream=True,
            headers={"Accept": "text/csv,application/octet-stream,*/*",
                     "User-Agent": "Mozilla/5.0 (compatible; DhanPayoffStudio/3.0)"},
        )
        if resp.status_code != 200:
            raise DhanDataError(f"{host}: HTTP {resp.status_code}")
        final = urlparse(resp.url)
        if final.scheme != "https" or not final.hostname:
            raise DhanDataError(f"{host}: instrument redirect was not HTTPS")
        size, chunks = 0, []
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > cap_bytes:
                raise DhanDataError(f"{host}: exceeded {cap_bytes // 1_000_000} MB safety limit")
            chunks.append(chunk)
        if not chunks:
            raise DhanDataError(f"{host}: empty CSV response")
        data = b"".join(chunks)
        if data.lstrip().lower().startswith((b"<html", b"<!doctype html")):
            raise DhanDataError(f"{host}: returned an HTML page instead of a CSV")
        return data
    except requests.exceptions.SSLError as exc:
        raise DhanDataError(f"{host}: SSL certificate validation failed") from exc
    except requests.exceptions.Timeout as exc:
        raise DhanDataError(f"{host}: download timed out") from exc
    except requests.RequestException as exc:
        raise DhanDataError(f"{host}: network error ({type(exc).__name__})") from exc
    finally:
        if "resp" in locals():
            resp.close()


def parse_dhan_equities(payload: bytes) -> dict[str, int]:
    """Derive unambiguous NSE EQ security IDs from official Dhan compact/detailed CSV.

    Detailed CSV no longer has SEM_TRADING_SYMBOL, per Dhan v2 docs. Inspect
    SYMBOL_NAME, display symbol and compact aliases instead. Never guess names
    from a derivative option contract, or accept different security IDs for
    the same cash symbol.
    """
    equities = {}
    conflicted = set()
    seen_equity_rows = 0
    for row in _csv_rows(payload):
        if _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper() != "NSE":
            continue
        if _cell(row, "SEGMENT", "SEM_SEGMENT").upper() not in ("E", "EQ", "NSE_EQ"):
            continue
        series = _cell(row, "SERIES", "SEM_SERIES").upper()
        if series and series not in ("EQ", "EQUITY"):
            continue
        secid = _positive_integer(_cell(row, "SECURITY_ID", "SEM_SMST_SECURITY_ID"))
        if not secid:
            continue
        seen_equity_rows += 1
        candidates = [
            _cell(row, "TRADING_SYMBOL", "SEM_TRADING_SYMBOL"),
            _cell(row, "SYMBOL_NAME", "SM_SYMBOL_NAME"),
            _cell(row, "DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"),
        ]
        for candidate in candidates:
            candidate = candidate.strip().upper()
            if candidate.endswith("-EQ"):
                candidate = candidate[:-3]
            # Multiword full company names are not NSE cash symbols.
            if not SYMBOL_PATTERN.fullmatch(candidate):
                continue
            old = equities.get(candidate)
            if old is not None and old != secid:
                conflicted.add(candidate)
            else:
                equities[candidate] = secid
    for symbol in conflicted:
        equities.pop(symbol, None)
    if not equities:
        raise DhanDataError(
            f"Dhan cash-equity master contained {seen_equity_rows} NSE_EQ records "
            "but no validated symbol/security ID mappings (check CSV columns)"
        )
    return equities


def match_cash_ids(underlyings: list[DhanUnderlying], equities: dict[str, int]) -> tuple[list[DhanUnderlying], int]:
    """Attach only verified NSE_EQ IDs, leaving unresolvable stocks unpriced."""
    mapped = []
    matched = 0
    for item in underlyings:
        found = equities.get(item.entry.symbol)
        # An old cached ID must not override a freshly verified mismatch.
        secid = found if found is not None else 0
        if secid:
            matched += 1
        mapped.append(DhanUnderlying(item.entry, secid))
    return mapped, matched


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
        secid = record.equity_security_id
        if (not SYMBOL_PATTERN.fullmatch(item.symbol)
                or item.symbol in seen_symbols
                or type(secid) is not int or secid < 0
                or (secid and secid in seen_ids)
                or not item.lots or any(type(v) is not int or v <= 0 for v in item.lots.values())):
            raise DhanDataError("Invalid/conflicting symbol, ID or lot in F&O list")
        seen_symbols.add(item.symbol)
        if secid:
            seen_ids.add(secid)
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


COMPACT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def resolve_equity_ids(required_symbols: set[str] | None = None) -> dict[str, int]:
    """Resolve Dhan NSE_EQ security IDs with a stock-universe overlap check.

    A segment CSV with hundreds of rows is not automatically a *usable* match.
    If a source has no matching stock symbols, try the next official source
    instead of returning a mapping that would make every LTP appear as N/A.
    """
    required = {x.strip().upper() for x in (required_symbols or set()) if x.strip()}
    diagnostics = []
    best_ids, best_overlap = {}, 0
    for name, url, limit in [
        ("Dhan NSE_EQ segment", EQ_SEGMENT_URL, 12_000_000),
        ("Dhan compact master", COMPACT_MASTER_URL, 40_000_000),
        ("Dhan detailed master", "https://images.dhan.co/api-data/api-scrip-master-detailed.csv", 80_000_000),
    ]:
        try:
            ids = parse_dhan_equities(_download_csv(url, limit, timeout=(5, 18)))
            if len(ids) < 100:
                raise DhanDataError(f"Only {len(ids)} NSE cash-equity symbols parsed")
            overlap = len(required.intersection(ids)) if required else len(ids)
            if overlap > best_overlap:
                best_ids, best_overlap = ids, overlap
            if required and overlap < max(1, len(required) // 2):
                diagnostics.append(
                    f"{name}: matched only {overlap}/{len(required)} F&O symbols"
                )
                continue
            return ids
        except (DhanDataError, ValueError) as exc:
            diagnostics.append(f"{name}: {exc}")
    if best_overlap >= max(1, len(required) // 4):
        return best_ids  # Show partial coverage rather than hide usable quotes.
    raise DhanDataError(
        "NSE cash-stock ID mapping unavailable or mismatched. "
        + " | ".join(diagnostics)[:900]
    )


def _save_catalogue(catalogue, label, path):
    _validate_catalogue(catalogue)
    _write_cache(catalogue, label, path)
    return catalogue, label


def load_fast_catalogue(refresh: bool = False, progress=None, cache_path: Path = CACHE_FILE):
    """Automatically load a complete NSE stock F&O list; never request CSV upload.

    Prioritize the *small official NSE lot-size file* to ensure the table can
    appear even if Dhan's market-data ID service is unreachable. Verified
    NSE_EQ IDs are resolved lazily when the owner requests Dhan prices.
    """
    def report(message):
        if progress:
            progress(message)

    cached = _read_cache(cache_path)
    if cached and cached[1] <= MAX_CACHE_AGE_SECONDS and not refresh:
        data, age, stamp, method = cached
        return data, f"Local cache ({age/3600:.1f} hours old; {method})"

    start = time.monotonic()
    errors = []

    # Independent of Dhan identity/marketfeed. Most important: show stock rows.
    report("Trying official NSE stock F&O lot-size file (no Dhan login needed)...")
    try:
        nse_payload = _download_csv(NSE_LOTS_URL, 3_000_000, timeout=(5, 14))
        lot_rows = parse_nse_lots(nse_payload)
        # Dhan security IDs not yet resolved = 0; never use 0 in marketfeed.
        catalogue = [DhanUnderlying(row, 0) for row in lot_rows]
        _validate_catalogue(catalogue)
        catalogue, method = _save_catalogue(
            catalogue, "NSE official F&O lots (Dhan NSE_EQ IDs resolved on price request)",
            cache_path,
        )
        report(f"Loaded {len(catalogue)} F&O stocks from NSE.")
        return catalogue, f"{method} · {time.monotonic() - start:.1f}s"
    except (DhanDataError, ValueError, UnicodeError) as exc:
        errors.append(f"NSE lot-size CSV: {exc}")

    # If NSE itself fails, attempt Dhan's two smaller segment sources.
    report("Official NSE lot file unavailable; checking Dhan NSE_EQ/NSE_FNO segments...")
    try:
        eq = parse_dhan_equities(
            _download_csv(EQ_SEGMENT_URL, 12_000_000, timeout=(5, 14))
        )
        result = parse_dhan_fno_segment(
            _download_csv(FNO_SEGMENT_URL, 80_000_000, timeout=(5, 22)), eq
        )
        _validate_catalogue(result)
        result, method = _save_catalogue(
            result, "Dhan NSE_EQ and NSE_FNO segment files", cache_path
        )
        return result, f"{method} · {time.monotonic() - start:.1f}s"
    except (DhanDataError, ValueError, UnicodeError) as exc:
        errors.append(f"Dhan segmented master: {exc}")

    # Last automatic public instrument-master option; only once on cold failure.
    report("Segment files unavailable; trying Dhan public detailed master...")
    try:
        from .dhan_market import fetch_dhan_master
        result, _ = fetch_dhan_master()
        result, method = _save_catalogue(
            result, "Dhan official detailed master", cache_path
        )
        return result, f"{method} · {time.monotonic() - start:.1f}s"
    except (DhanDataError, ValueError, UnicodeError) as exc:
        errors.append(f"Dhan detailed master: {exc}")

    if cached:
        data, age, _, method = cached
        report(f"Sources unavailable: reusing labelled {age/3600:.1f}-hour-old cache.")
        return data, f"STALE CACHE ({age/3600:.1f}h; {method}). Check exchange lots before trading."
    raise DhanDataError(
        "Automatic list download failed at all official sources. "
        + " | ".join(errors)[:1300]
        + " · No file upload required. Check source-specific HTTP, SSL or timeout errors."
    )
