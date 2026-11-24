"""DhanHQ instrument/market-data reader for the local F&O screener only.

One market-data POST endpoint (/marketfeed/ltp) is permitted. No portfolio,
order, margin, auth-refresh or account endpoints are used.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .screener import Quote, Underlying, MONTHS, SYMBOL_PATTERN

DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"
INDIA_TIMEZONE = timezone.utc


class DhanDataError(ValueError):
    """Market data unavailable, invalid, or unsafe to interpret."""


@dataclass(frozen=True)
class DhanUnderlying:
    entry: Underlying
    equity_security_id: int


def _cell(row, *keys):
    for name in keys:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _expiry_month(raw):
    if not raw or raw in ("-", "NA"):
        return None
    raw = raw.strip()
    for pat in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%Y",
                "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(raw[:19], pat)
            if 2020 <= dt.year <= 2100:
                return f"{MONTHS[dt.month - 1]}-{dt.year % 100:02d}"
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"{MONTHS[dt.month - 1]}-{dt.year % 100:02d}"
    except ValueError:
        return None


def _positive_integer(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0 or value != int(value):
        return None
    return int(value)


def parse_dhan_master(data: str | bytes) -> list[DhanUnderlying]:
    """Map Dhan NSE stock F&O derivatives to verified NSE_EQ security IDs.

    Lot sizes are selected by expiry month. Conflicting lots for a stock-month
    are excluded rather than silently guessed. Derivatives without a verified
    NSE_EQ underlying are excluded, not queried on a different segment.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            data = data.decode("cp1252")
    if not isinstance(data, str) or not data.strip() or "\x00" in data:
        raise DhanDataError("Invalid instrument master CSV")
    reader = csv.DictReader(io.StringIO(data.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise DhanDataError("Dhan instrument CSV has no header")
    reader.fieldnames = [name.strip().upper().lstrip("\ufeff") for name in reader.fieldnames]
    fields = set(reader.fieldnames)
    if not (fields.intersection({"EXCH_ID", "SEM_EXM_EXCH_ID"})
            and fields.intersection({"SEGMENT", "SEM_SEGMENT"})
            and fields.intersection({"SECURITY_ID", "SEM_SMST_SECURITY_ID"})
            and fields.intersection({"INSTRUMENT", "SEM_INSTRUMENT_NAME"})
            and fields.intersection({"LOT_SIZE", "SEM_LOT_UNITS"})
            and fields.intersection({"SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"})
            and "UNDERLYING_SYMBOL" in fields):
        raise DhanDataError("Unexpected Dhan detailed CSV schema (underlying/lot/expiry/security fields missing)")
    records = list(reader)
    if len(records) < 2:
        raise DhanDataError("Dhan instrument list is empty")
    eq_by_id = {}
    eq_by_symbol = {}
    for row in records:
        exch = _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper()
        seg = _cell(row, "SEGMENT", "SEM_SEGMENT").upper()
        if exch != "NSE" or seg not in ("E", "EQ", "NSE_EQ"):
            continue
        series = _cell(row, "SERIES", "SEM_SERIES").upper()
        if series and series not in ("EQ", "EQUITY"):
            continue
        security_id = _positive_integer(_cell(row, "SECURITY_ID", "SEM_SMST_SECURITY_ID"))
        sym = _cell(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL_NAME", "SM_SYMBOL_NAME").upper()
        if not security_id or not SYMBOL_PATTERN.fullmatch(sym):
            continue
        eq_by_id[security_id] = sym
        eq_by_symbol[sym] = security_id
    if not eq_by_id:
        raise DhanDataError("No NSE_EQ equity IDs found in Dhan master")

    grouped = {}
    conflicts = set()
    for row in records:
        exch = _cell(row, "EXCH_ID", "SEM_EXM_EXCH_ID").upper()
        seg = _cell(row, "SEGMENT", "SEM_SEGMENT").upper()
        inst = (_cell(row, "INSTRUMENT", "SEM_INSTRUMENT_NAME") + " "
                + _cell(row, "INSTRUMENT_TYPE", "SEM_EXCH_INSTRUMENT_TYPE")).upper()
        if exch != "NSE" or seg not in ("D", "FNO", "NSE_FNO"):
            continue
        if "STK" not in inst or not ("OPT" in inst or "FUT" in inst):
            continue
        sym = _cell(row, "UNDERLYING_SYMBOL").upper()
        if not SYMBOL_PATTERN.fullmatch(sym):
            continue
        raw_under_id = _cell(row, "UNDERLYING_SECURITY_ID")
        under_id = _positive_integer(raw_under_id)
        if raw_under_id and not under_id:
            # An explicitly invalid underlying ID must not fall back to a guess.
            continue
        if under_id:
            if under_id not in eq_by_id or eq_by_id[under_id] != sym:
                # Refuse inconsistent underlying ID/symbol evidence.
                continue
            equity_id = under_id
        else:
            equity_id = eq_by_symbol.get(sym)
        if not equity_id:
            continue
        expiry = _expiry_month(_cell(row, "SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"))
        lot = _positive_integer(_cell(row, "LOT_SIZE", "SEM_LOT_UNITS"))
        if not expiry or not lot:
            continue
        key = (sym, expiry)
        old = grouped.get(key)
        if old and old[1] != lot:
            conflicts.add(key)
            continue
        grouped[key] = (equity_id, lot)
    stocks = {}
    for (sym, expiry), (security_id, lot) in grouped.items():
        if (sym, expiry) in conflicts:
            continue
        s = stocks.setdefault(sym, {"security_id": security_id, "lots": {}})
        if s["security_id"] != security_id:
            raise DhanDataError("Inconsistent equity security IDs in master")
        s["lots"][expiry] = lot
    result = [
        DhanUnderlying(Underlying(sym, sym, details["lots"]), details["security_id"])
        for sym, details in sorted(stocks.items()) if details["lots"]
    ]
    if not result:
        raise DhanDataError("No verified stock F&O + NSE_EQ symbol mappings in Dhan master")
    return result


def fetch_dhan_master() -> tuple[list[DhanUnderlying], str]:
    import requests

    from urllib.parse import urlparse
    try:
        response = requests.get(
            DHAN_MASTER_URL, timeout=(6, 26), allow_redirects=True,
            headers={"Accept": "text/csv,application/octet-stream,*/*",
                     "User-Agent": "Mozilla/5.0 (DhanPayoffStudio/3.0)"})
        if response.status_code != 200:
            raise DhanDataError(f"Dhan detailed master returned HTTP {response.status_code}")
    except requests.exceptions.SSLError as exc:
        raise DhanDataError("Dhan detailed master SSL certificate verification failed") from exc
    except requests.exceptions.Timeout as exc:
        raise DhanDataError("Dhan detailed master download timed out") from exc
    except requests.RequestException as exc:
        raise DhanDataError(f"Dhan detailed master network error ({type(exc).__name__})") from exc
    final = urlparse(response.url)
    if final.scheme != "https" or not final.hostname:
        raise DhanDataError("Dhan detailed master redirected away from HTTPS")
    if not response.content or len(response.content) > 80_000_000:
        raise DhanDataError("Dhan detailed master empty or too large")
    rows = parse_dhan_master(response.content)
    if len(rows) < 100:
        raise DhanDataError("Fewer than 100 stock F&O symbols verified; refusing incomplete catalogue")
    return rows, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class _RateLimiter:
    """One market-quote request per second per local Streamlit process."""
    def __init__(self):
        self.lock = threading.Lock()
        self.next_at = 0.0

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            if delay:
                time.sleep(delay)
            self.next_at = time.monotonic() + 1.1


QUOTE_LIMITER = _RateLimiter()


def parse_dhan_ltp_response(payload: dict, id_to_symbol: dict[int, str], timestamp: str) -> dict[str, Quote]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        # Dhan returns failure codes inside JSON, sometimes with HTTP 200.
        # Report only short error identifiers: never print raw response data.
        codes = []
        if isinstance(payload, dict):
            for key in ("errorCode", "error_code", "code"):
                value = payload.get(key)
                if isinstance(value, (str, int)) and re.fullmatch(r"[A-Za-z0-9-]{1,32}", str(value)):
                    codes.append(str(value))
            data = payload.get("data")
            if isinstance(data, dict):
                for key in data:
                    if re.fullmatch(r"[A-Za-z0-9-]{1,32}", str(key)):
                        codes.append(str(key))
                    if len(codes) >= 3:
                        break
        suffix = " · codes: " + ", ".join(codes[:3]) if codes else ""
        raise DhanDataError("Dhan rejected LTP request" + suffix + "; "
                            "check token, Client ID and Data API entitlement")
    data = payload.get("data")
    market = data.get("NSE_EQ") if isinstance(data, dict) else None
    if not isinstance(market, dict):
        raise DhanDataError("Dhan LTP response missing NSE_EQ market data")
    quotes = {}
    for security_id, symbol in id_to_symbol.items():
        quote = market.get(str(security_id))
        if not isinstance(quote, dict):
            continue
        try:
            price = Decimal(str(quote.get("last_price")))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if not price.is_finite() or price <= 0:
            continue
        quotes[symbol] = Quote(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                               timestamp, "Dhan NSE_EQ LTP (snapshot)")
    return quotes


def fetch_dhan_ltp(underlyings: list[DhanUnderlying], client_id: str,
                   token: str, batch_size: int = 800) -> tuple[dict[str, Quote], list[str]]:
    """Read-only market snapshots; never store or log token; no fallback fake prices."""
    import requests

    cid = client_id.strip()
    secret = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{5,25}", cid) or not (16 <= len(secret) <= 8192):
        raise DhanDataError("Enter a valid Dhan client ID and local access token")
    if not 1 <= batch_size <= 1000:
        raise ValueError("Market quote batch limit must be 1 to 1000")
    unique_ids = {}
    for item in underlyings:
        if item.equity_security_id in unique_ids and unique_ids[item.equity_security_id] != item.entry.symbol:
            raise DhanDataError("Conflicting NSE_EQ instrument identifiers")
        unique_ids[item.equity_security_id] = item.entry.symbol
    ordered = sorted(unique_ids)
    headers = {
        "Accept": "application/json", "Content-Type": "application/json",
        "client-id": cid, "access-token": secret,
    }
    output, errors = {}, []
    for offset in range(0, len(ordered), batch_size):
        ids = ordered[offset:offset + batch_size]
        QUOTE_LIMITER.acquire()
        try:
            response = requests.post(
                DHAN_LTP_URL, headers=headers, json={"NSE_EQ": ids}, timeout=(7, 22),
            )
            status = response.status_code
            if status in (401, 403):
                raise DhanDataError("Dhan rejected the token/client ID or Data API permission (HTTP 401/403)")
            if status == 429:
                raise DhanDataError("Dhan quote rate limit encountered (HTTP 429); refresh later")
            response.raise_for_status()
            try:
                packet = response.json()
            except ValueError as exc:
                raise DhanDataError("Dhan LTP response was not valid JSON") from exc
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            quotes = parse_dhan_ltp_response(
                packet, {sid: unique_ids[sid] for sid in ids}, stamp,
            )
            if not quotes:
                errors.append(
                    f"Batch {offset // batch_size + 1}: Dhan returned no valid NSE_EQ "
                    f"prices for {len(ids)} requested IDs. Check cash-ID mapping "
                    "or market-data entitlement; no quote is fabricated."
                )
            output.update(quotes)
        except DhanDataError as exc:
            errors.append(f"Batch {offset // batch_size + 1}: {exc}")
            if "token" in str(exc).lower() or "429" in str(exc):
                break
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"Batch {offset // batch_size + 1}: Dhan data request failed ({type(exc).__name__})")
    return output, errors
