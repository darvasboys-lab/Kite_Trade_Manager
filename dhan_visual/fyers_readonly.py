"""FYERS individual NON-TRADING app: OAuth and market-data-only REST adapter.

Only three read-only GET endpoints and one OAuth code exchange are supported:
  /data/quotes, /data/options-chain-v3, /data/history
  /api/v3/validate-authcode
There are deliberately NO FYERS order, holdings, trade or mutation methods.
Secrets/tokens may be kept in Windows Credential Manager, never source/JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import re
import secrets
from threading import RLock
import time as clock
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .auto_auth import AutoAuthError, _keyring
from .screener import Quote
from .api_monitor import record

INDIA = timezone(timedelta(hours=5, minutes=30))
REDIRECT_URI = "https://trade.fyers.in/api-login/redirect-uri/index.html"
API_ROOT = "https://api-t1.fyers.in"
VAULT_SERVICE = "CoveredCallBuilder.FYERS.DataOnly.v1"
APP_ID_RE = re.compile(r"^[A-Za-z0-9]{4,32}-100$")
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,39}$")
MAX_CHAIN_STRIKES = 50
MIN_API_INTERVAL = 1.5   # Standard: 5/sec, 50/min; intentionally more conservative
LOCK = RLock()
_LAST_CALL = 0.0


class FyersDataError(ValueError):
    """Safe, redacted API error. Never append broker response containing secrets."""


@dataclass(frozen=True)
class FyersAccess:
    app_id: str
    token: str
    expires_at: datetime

    def valid(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return bool(self.token and self.expires_at > now + timedelta(minutes=2))


@dataclass(frozen=True)
class FyersCall:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_ltp: Decimal
    call_ltp: Decimal
    premium_pct: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int = 0


@dataclass(frozen=True)
class FyersStraddle:
    symbol: str
    expiry: str
    strike: Decimal
    underlying_ltp: Decimal
    call_ltp: Decimal
    put_ltp: Decimal
    premium_pct: Decimal
    call_bid: Decimal | None = None
    put_bid: Decimal | None = None


def validate_app_id(raw: str) -> str:
    if not isinstance(raw, str) or not APP_ID_RE.fullmatch(raw.strip()):
        raise FyersDataError("Enter the FYERS non-trading App ID (ending in -100).")
    return raw.strip()


def _valid_secret(raw: str) -> str:
    if not isinstance(raw, str) or not 4 <= len(raw.strip()) <= 256:
        raise FyersDataError("Enter the App Secret from your FYERS API dashboard.")
    return raw.strip()


def next_token_expiry(now: datetime | None = None) -> datetime:
    """FYERS API token expires at the next 06:30 IST boundary."""
    local = (now or datetime.now(timezone.utc)).astimezone(INDIA)
    cutoff = datetime.combine(local.date(), time(6, 30), INDIA)
    if local >= cutoff:
        cutoff += timedelta(days=1)
    return cutoff.astimezone(timezone.utc)


def save_app_to_vault(app_id: str, app_secret: str) -> None:
    app_id, secret = validate_app_id(app_id), _valid_secret(app_secret)
    try:
        _keyring().set_password(
            VAULT_SERVICE, "app",
            json.dumps({"v": 1, "id": app_id, "secret": secret}),
        )
    except Exception as exc:
        raise FyersDataError("Windows Credential Manager could not store FYERS settings.") from exc


def read_app_from_vault() -> tuple[str, str] | None:
    try:
        raw = _keyring().get_password(VAULT_SERVICE, "app")
        payload = json.loads(raw) if raw else None
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        return validate_app_id(payload["id"]), _valid_secret(payload["secret"])
    except Exception:
        return None


def save_access_to_vault(access: FyersAccess) -> None:
    if not access.valid():
        raise FyersDataError("Cannot save an expired FYERS access token.")
    try:
        _keyring().set_password(
            VAULT_SERVICE, "access",
            json.dumps({"v": 1, "id": access.app_id, "token": access.token,
                        "expiry": access.expires_at.isoformat()}),
        )
    except Exception as exc:
        raise FyersDataError("Windows Credential Manager could not store the FYERS session.") from exc


def read_access_from_vault() -> FyersAccess | None:
    try:
        raw = _keyring().get_password(VAULT_SERVICE, "access")
        payload = json.loads(raw) if raw else None
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        app_id = validate_app_id(payload["id"])
        access = FyersAccess(app_id, payload["token"],
                             datetime.fromisoformat(payload["expiry"]))
        return access if access.valid() else None
    except Exception:
        return None


def forget_vault() -> None:
    try:
        ring = _keyring()
        for label in ("app", "access"):
            try:
                ring.delete_password(VAULT_SERVICE, label)
            except Exception:
                pass
    except Exception:
        pass


def authorization_url(app_id: str, state: str) -> str:
    validate_app_id(app_id)
    if not isinstance(state, str) or not 24 <= len(state) <= 256:
        raise FyersDataError("An invalid login request was generated; restart FYERS login.")
    return (API_ROOT + "/api/v3/generate-authcode?" + urlencode({
        "client_id": app_id, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "state": state,
    }))


def new_state() -> str:
    return secrets.token_urlsafe(32)


def parse_authorization_return(full_url: str, expected_state: str) -> str:
    """Accept only exact registered FYERS redirect URI and state-matched OAuth."""
    if not isinstance(full_url, str) or len(full_url) > 8192:
        raise FyersDataError("Paste the full redirected FYERS URL.")
    parts = urlsplit(full_url.strip())
    origin = urlsplit(REDIRECT_URI)
    if (parts.scheme, parts.netloc, parts.path) != (
            origin.scheme, origin.netloc, origin.path):
        raise FyersDataError("Paste the URL from the registered FYERS redirect page.")
    try:
        params = parse_qs(parts.query, strict_parsing=True)
    except ValueError as exc:
        raise FyersDataError("FYERS redirect query is invalid.") from exc
    if len(params.get("state", [])) != 1 or not hmac.compare_digest(
        params["state"][0], expected_state or "",
    ):
        raise FyersDataError("FYERS login state mismatch; start the login again.")
    codes = params.get("auth_code", [])
    if len(codes) != 1 or not 10 <= len(codes[0]) <= 4096:
        raise FyersDataError("FYERS redirect did not contain a valid authorization code.")
    return codes[0]


def exchange_code(app_id: str, app_secret: str, code: str, *,
                  post=None, now: datetime | None = None) -> FyersAccess:
    app_id, secret = validate_app_id(app_id), _valid_secret(app_secret)
    if not isinstance(code, str) or not 10 <= len(code) <= 4096:
        raise FyersDataError("FYERS authorization code is invalid.")
    hashed = sha256((app_id + ":" + secret).encode()).hexdigest()
    try:
        sender = post or requests.post
        response = sender(
            API_ROOT + "/api/v3/validate-authcode",
            json={"grant_type": "authorization_code", "appIdHash": hashed,
                  "code": code},
            timeout=(5, 15), allow_redirects=False,
        )
        if response.status_code != 200:
            raise FyersDataError(
                f"FYERS login failed (HTTP {response.status_code}). "
                "Confirm App ID, Secret and Redirect URL, then log in again."
            )
        result = response.json()
        if not isinstance(result, dict) or result.get("s") != "ok" or not result.get("access_token"):
            raise FyersDataError(
                "FYERS rejected this login. Re-authorize with your own non-trading app."
            )
        access = FyersAccess(app_id, str(result["access_token"]),
                             next_token_expiry(now))
        if not access.valid(now):
            raise FyersDataError("FYERS returned an already-expired session.")
        return access
    except requests.RequestException as exc:
        raise FyersDataError("Cannot reach FYERS authentication service.") from exc
    except (TypeError, ValueError) as exc:
        if isinstance(exc, FyersDataError):
            raise
        raise FyersDataError("FYERS authentication response was invalid.") from exc


def _positive(raw) -> Decimal | None:
    try:
        val = Decimal(str(raw))
        return val if val.is_finite() and val > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _nonnegative(raw) -> Decimal | None:
    try:
        val = Decimal(str(raw))
        return val if val.is_finite() and val >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def nse_equity_symbol(ticker: str) -> str:
    if not isinstance(ticker, str) or not SYMBOL_RE.fullmatch(ticker):
        raise FyersDataError("Invalid NSE equity ticker.")
    return "NSE:" + ticker + "-EQ"


def _rate_gate():
    """One shared bounded FYERS data budget across home, scanner and history."""
    global _LAST_CALL
    with LOCK:
        pause = MIN_API_INTERVAL - (clock.monotonic() - _LAST_CALL)
        if pause > 0:
            clock.sleep(pause)
        _LAST_CALL = clock.monotonic()


def _get(access: FyersAccess, route: str, params: dict, *, get=None) -> dict:
    if not isinstance(access, FyersAccess) or not access.valid():
        raise FyersDataError("FYERS login expired at 6:30 AM IST; reconnect in Settings.")
    if route not in ("/data/quotes", "/data/options-chain-v3", "/data/history"):
        raise FyersDataError("Unsupported read-only FYERS route.")
    _rate_gate()
    record("FYERS")
    try:
        requester = get or requests.get
        response = requester(
            API_ROOT + route, params=params,
            headers={"Authorization": access.app_id + ":" + access.token,
                     "Accept": "application/json"},
            timeout=(5, 16), allow_redirects=False,
        )
        if response.status_code in (401, 403):
            raise FyersDataError("FYERS session unauthorized; please reconnect in Settings.")
        if response.status_code == 429:
            record("FYERS", "rate_limit")
            raise FyersDataError("FYERS data rate limit (429). Stop and retry later.")
        if response.status_code != 200:
            raise FyersDataError(f"FYERS market data HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("s") != "ok":
            code = payload.get("code") if isinstance(payload, dict) else None
            if code in (-8, -15, -16, -17):
                raise FyersDataError("FYERS session expired; reconnect in Settings.")
            raise FyersDataError("FYERS market data unavailable for requested instruments.")
        record("FYERS", "success")
        return payload
    except requests.RequestException as exc:
        raise FyersDataError("FYERS market-data service is unreachable.") from exc
    except (ValueError, TypeError) as exc:
        if isinstance(exc, FyersDataError):
            raise
        raise FyersDataError("FYERS returned an unreadable market-data response.") from exc


def parse_quotes(payload: dict, tickers: list[str], stamp: str) -> dict[str, Quote]:
    wanted = set(tickers)
    result = {}
    rows = payload.get("d")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict) or row.get("s") != "ok":
            continue
        ticker = row.get("n")
        if not isinstance(ticker, str) or not ticker.startswith("NSE:") or not ticker.endswith("-EQ"):
            continue
        symbol = ticker[4:-3]
        if symbol not in wanted:
            continue
        fields = row.get("v")
        price = _positive(fields.get("lp")) if isinstance(fields, dict) else None
        if price is not None:
            result[symbol] = Quote(price, stamp, "FYERS stock LTP")
    return result


def fetch_cash_quotes(access: FyersAccess, tickers: list[str], *,
                      get=None, progress=None) -> dict[str, Quote]:
    wanted = list(dict.fromkeys(t for t in tickers if SYMBOL_RE.fullmatch(t)))
    output = {}
    stamp = datetime.now(INDIA).isoformat(timespec="seconds")
    for offset in range(0, len(wanted), 50):
        batch = wanted[offset:offset + 50]
        response = _get(access, "/data/quotes",
                        {"symbols": ",".join(nse_equity_symbol(t) for t in batch)},
                        get=get)
        output.update(parse_quotes(response, batch, stamp))
        if progress is not None:
            progress(offset + len(batch), len(wanted), len(output))
    return output


def fetch_futures_quotes(access: FyersAccess, tickers: list[str], expiry: str, *,
                         get=None) -> dict[str, Quote]:
    """Exact monthly NSE futures LTP, batched through the shared read-only gate."""
    if not isinstance(expiry, str) or not re.fullmatch(
            r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-[0-9]{2}", expiry):
        raise FyersDataError("Invalid futures expiry month.")
    month, year = expiry.split("-")
    wanted = {
        "NSE:" + t + year + month + "FUT": t
        for t in tickers if isinstance(t, str) and SYMBOL_RE.fullmatch(t)
    }
    output = {}
    names = list(wanted)
    for offset in range(0, len(names), 50):
        batch = names[offset:offset + 50]
        payload = _get(access, "/data/quotes", {"symbols": ",".join(batch)}, get=get)
        stamp = datetime.now(INDIA).isoformat(timespec="seconds")
        rows = payload.get("d")
        if not isinstance(rows, list):
            continue
        allowed = set(batch)
        for row in rows:
            if not isinstance(row, dict) or row.get("s") != "ok":
                continue
            name = row.get("n")
            if not isinstance(name, str) or name not in allowed:
                continue
            fields = row.get("v")
            price = _positive(fields.get("lp")) if isinstance(fields, dict) else None
            if price is not None:
                output[wanted[name]] = Quote(price, stamp, "FYERS futures LTP")
    return output


def _select_expiry(expiries: list, requested_month: str) -> tuple[str, date] | None:
    try:
        month, yy = requested_month.upper().split("-")
        year = 2000 + int(yy)
        mon = datetime.strptime(month, "%b").month
    except (ValueError, TypeError, AttributeError):
        return None
    valid = []
    for entry in expiries:
        if not isinstance(entry, dict):
            continue
        try:
            day = datetime.strptime(str(entry.get("date")), "%d-%m-%Y").date()
            stamp = str(entry.get("expiry") or "")
            if day.year == year and day.month == mon and stamp.isdigit():
                valid.append((entry.get("expiry_flag") == "M", day, stamp))
        except ValueError:
            continue
    if not valid:
        return None
    # Explicit monthly flag wins; otherwise the latest exchange-listed
    # expiry of the requested month wins (never silently use another month).
    valid.sort()
    best = valid[-1]
    return best[2], best[1]


def resolve_expiry_timestamp(access: FyersAccess, expiry: str, *,
                             probe: str = "SBIN", get=None) -> tuple[str, str]:
    payload = _get(access, "/data/options-chain-v3", {
        "symbol": nse_equity_symbol(probe), "strikecount": 1, "timestamp": "",
        "greeks": "0",
    }, get=get)
    inner = payload.get("data")
    if not isinstance(inner, dict) or not isinstance(inner.get("expiryData"), list):
        raise FyersDataError("FYERS did not return its available expiry list.")
    match = _select_expiry(inner["expiryData"], expiry)
    if match is None:
        raise FyersDataError(f"FYERS has no verifiable stock option expiry for {expiry}.")
    return match[0], match[1].isoformat()


def parse_chain(payload: dict, symbol: str, expiry: str) -> tuple[FyersCall | None, FyersStraddle | None]:
    """Read genuine same-request spot, CE and PE. No invented/fallback premiums."""
    inner = payload.get("data")
    chain = inner.get("optionsChain") if isinstance(inner, dict) else None
    if not isinstance(chain, list) or not chain or len(chain) > 500:
        return None, None
    correct_underlying = nse_equity_symbol(symbol)
    spot_entries = [
        _positive(e.get("ltp")) for e in chain
        if isinstance(e, dict) and e.get("option_type") == ""
        and e.get("symbol") == correct_underlying
        and str(e.get("strike_price")) in ("-1", "-1.0")
    ]
    if len(spot_entries) != 1 or not spot_entries[0]:
        return None, None
    spot = spot_entries[0]
    by_strike: dict[Decimal, dict] = {}
    for item in chain:
        if not isinstance(item, dict):
            continue
        opt = item.get("option_type")
        if opt not in ("CE", "PE"):
            continue
        exchange_sym = item.get("symbol")
        if (not isinstance(exchange_sym, str)
                or not exchange_sym.startswith("NSE:" + symbol)
                or not exchange_sym.endswith(opt)):
            continue
        strike = _positive(item.get("strike_price"))
        price = _positive(item.get("ltp"))
        if strike is None or price is None:
            continue
        contracts = by_strike.setdefault(strike, {})
        if opt in contracts:
            # Ambiguous duplicate leg: fail-closed for this strike.
            contracts[opt] = None
        else:
            contracts[opt] = item
    otm_candidates = sorted(
        s for s, legs in by_strike.items()
        if s >= spot * Decimal("1.05") and isinstance(legs.get("CE"), dict)
    )
    call = None
    if otm_candidates:
        strike = otm_candidates[0]
        leg = by_strike[strike]["CE"]
        close = _positive(leg["ltp"])
        if close:
            try:
                volume = max(0, int(leg.get("volume") or 0))
            except (TypeError, ValueError):
                volume = 0
            call = FyersCall(
                symbol, expiry, strike, spot, close,
                (close * 100 / spot).quantize(Decimal("0.01")),
                _nonnegative(leg.get("bid")), _nonnegative(leg.get("ask")), volume,
            )
    atm_straddle = None
    strikes = sorted(by_strike, key=lambda x: (abs(x - spot), x))
    if strikes:
        nearest = strikes[0]
        legs = by_strike[nearest]
        ce, pe = legs.get("CE"), legs.get("PE")
        if isinstance(ce, dict) and isinstance(pe, dict):
            ce_ltp, pe_ltp = _positive(ce.get("ltp")), _positive(pe.get("ltp"))
            if ce_ltp and pe_ltp:
                atm_straddle = FyersStraddle(
                    symbol, expiry, nearest, spot, ce_ltp, pe_ltp,
                    ((ce_ltp + pe_ltp) * 100 / spot).quantize(Decimal("0.01")),
                    _nonnegative(ce.get("bid")), _nonnegative(pe.get("bid")),
                )
    return call, atm_straddle


def fetch_chain(access: FyersAccess, symbol: str, expiry: str,
                expiry_timestamp: str, *, get=None):
    if not isinstance(expiry_timestamp, str) or not expiry_timestamp.isdigit():
        raise FyersDataError("FYERS selected expiry timestamp is invalid.")
    payload = _get(access, "/data/options-chain-v3", {
        "symbol": nse_equity_symbol(symbol), "strikecount": MAX_CHAIN_STRIKES,
        "timestamp": expiry_timestamp, "greeks": "0",
    }, get=get)
    return parse_chain(payload, symbol, expiry)


def fetch_daily_levels(access: FyersAccess, ticker: str, *,
                       day: date | None = None, get=None):
    """One <=366-day history GET per stock for 200-EMA and previous-month R1."""
    import pandas as pd
    from .technical_indicators import calculate_levels
    day = day or datetime.now(INDIA).date()
    # A current-day daily candle can be partially formed while NSE is open.
    # Use only completed prior-day bars for EMA and previous-month pivots.
    end = day - timedelta(days=1)
    first = end - timedelta(days=359)
    resp = _get(access, "/data/history", {
        "symbol": nse_equity_symbol(ticker), "resolution": "D",
        "date_format": "1", "range_from": first.isoformat(),
        "range_to": end.isoformat(), "cont_flag": "1",
    }, get=get)
    rows = resp.get("candles")
    if not isinstance(rows, list) or len(rows) > 400:
        raise FyersDataError("FYERS did not return bounded daily candles.")
    valid = []
    for rec in rows:
        if not isinstance(rec, list) or len(rec) < 6:
            continue
        try:
            epoch = int(rec[0])
            parsed = datetime.fromtimestamp(epoch, INDIA).date()
            values = [_positive(v) for v in rec[1:5]]
            if (first <= parsed <= end and all(x is not None for x in values)
                    and values[1] >= values[2]):
                valid.append((pd.Timestamp(parsed), *(float(v) for v in values)))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    if len(valid) < 10:
        raise FyersDataError("FYERS daily history is insufficient for indicators.")
    frame = pd.DataFrame(valid, columns=["Date", "Open", "High", "Low", "Close"])
    frame = frame.set_index("Date")
    return calculate_levels(frame, current_month=day)


def fetch_r1_intraday_bars(access: FyersAccess, ticker: str, *,
                           day: date | None = None, now: datetime | None = None,
                           get=None):
    """FYERS genuine completed 15-minute candles for today's R1 crossover.

    Prior-session bars are retained solely for gap-up validation. We never
    assume a high or LTP alone proves an intraday cross.
    """
    import pandas as pd
    day = day or datetime.now(INDIA).date()
    local_now = (now or datetime.now(INDIA)).astimezone(INDIA)
    if day != local_now.date():
        raise FyersDataError("Intraday crossover can only be evaluated for today's date.")
    first = datetime.combine(day - timedelta(days=7), time(9, 15), INDIA)
    last_completed_start = local_now - timedelta(minutes=15)
    if last_completed_start <= first:
        raise FyersDataError("No completed FYERS 15-minute candles available yet.")
    response = _get(access, "/data/history", {
        "symbol": nse_equity_symbol(ticker), "resolution": "15",
        "date_format": "0",
        "range_from": str(int(first.timestamp())),
        "range_to": str(int(local_now.timestamp())),
        "cont_flag": "1",
    }, get=get)
    rows = response.get("candles")
    if not isinstance(rows, list) or len(rows) > 600:
        raise FyersDataError("FYERS intraday response missing/beyond expected size.")
    values = []
    for bar in rows:
        if not isinstance(bar, list) or len(bar) < 6:
            continue
        try:
            stamp = datetime.fromtimestamp(int(bar[0]), INDIA)
            o, h, low, close = [_positive(v) for v in bar[1:5]]
            if (first <= stamp <= last_completed_start
                    and o and h and low and close
                    and h >= low and h >= o and h >= close):
                values.append((pd.Timestamp(stamp), float(o), float(h),
                               float(low), float(close)))
        except (OSError, ValueError, TypeError, OverflowError):
            continue
    if not values:
        raise FyersDataError("No verified, completed FYERS 15-minute OHLC bars.")
    return pd.DataFrame(
        values, columns=["Date", "Open", "High", "Low", "Close"],
    ).set_index("Date")


def fetch_strategy_expiries(access, symbol, months, *, get=None):
    """Resolve the selected stock's verified monthly expiry dates in one request."""
    payload = _get(access, "/data/options-chain-v3", {
        "symbol": nse_equity_symbol(symbol), "strikecount": 1,
        "timestamp": "", "greeks": "0",
    }, get=get)
    inner = payload.get("data")
    if not isinstance(inner, dict) or not isinstance(inner.get("expiryData"), list):
        raise FyersDataError("FYERS did not return available expiry dates.")
    result = {}
    for month in months:
        match = _select_expiry(inner["expiryData"], month)
        if match and match[1] >= datetime.now(INDIA).date():
            result[month] = (match[0], match[1].isoformat())
    if not result:
        raise FyersDataError("No verified unexpired monthly option contracts.")
    return result


def fetch_strategy_quotes(access, symbol, months, *, get=None):
    """One paced quote request for equity and up to three exact monthly futures."""
    equity = nse_equity_symbol(symbol)
    names = {equity: "SPOT"}
    for expiry in list(months)[:3]:
        if not re.fullmatch(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-[0-9]{2}", expiry):
            raise FyersDataError("Invalid strategy futures month.")
        month, year = expiry.split("-")
        names[f"NSE:{symbol}{year}{month}FUT"] = expiry
    payload = _get(access, "/data/quotes", {"symbols": ",".join(names)}, get=get)
    stamp = datetime.now(INDIA).isoformat(timespec="seconds")
    result = {}
    rows = payload.get("d")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("s") != "ok":
            continue
        name = row.get("n")
        if not isinstance(name, str) or name not in names:
            continue
        fields = row.get("v")
        price = _positive(fields.get("lp")) if isinstance(fields, dict) else None
        if price is not None:
            result[names[name]] = Quote(price, stamp, "FYERS LTP")
    return result


def parse_strategy_chain(payload, symbol, expiry):
    """Return every unambiguous quoted strike from the requested monthly chain."""
    month, year = expiry.split("-")
    prefix = nse_equity_symbol(symbol)[:-3] + year + month
    inner = payload.get("data")
    chain = inner.get("optionsChain") if isinstance(inner, dict) else None
    if not isinstance(chain, list) or not chain or len(chain) > 500:
        raise FyersDataError("FYERS option chain unavailable.")
    rows, seen, duplicates = {}, set(), set()
    for item in chain:
        if not isinstance(item, dict):
            continue
        kind = item.get("option_type")
        name = item.get("symbol")
        strike = _positive(item.get("strike_price"))
        if kind not in ("CE", "PE") or strike is None or not isinstance(name, str):
            continue
        if not name.startswith(prefix) or not name.endswith(kind):
            continue
        encoded = _positive(name[len(prefix):-2])
        if encoded != strike:
            continue
        key = (strike, kind)
        if key in seen:
            duplicates.add(key)
            continue
        seen.add(key)
        rows[key] = dict(symbol=name, strike=strike, kind=kind,
                         ltp=_positive(item.get("ltp")),
                         bid=_nonnegative(item.get("bid")),
                         ask=_nonnegative(item.get("ask")),
                         oi=_nonnegative(item.get("oi")),
                         volume=_nonnegative(item.get("volume")))
    for key in duplicates:
        rows.pop(key, None)
    if not rows:
        raise FyersDataError("No verifiable monthly option strikes in FYERS response.")
    return rows


def fetch_strategy_chain(access, symbol, expiry, epoch, *, get=None):
    if not isinstance(epoch, str) or not epoch.isdigit():
        raise FyersDataError("Invalid strategy expiry timestamp.")
    payload = _get(access, "/data/options-chain-v3", {
        "symbol": nse_equity_symbol(symbol), "strikecount": MAX_CHAIN_STRIKES,
        "timestamp": epoch, "greeks": "0",
    }, get=get)
    return parse_strategy_chain(payload, symbol, expiry)
