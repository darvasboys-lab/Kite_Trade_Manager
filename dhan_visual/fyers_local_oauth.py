"""Opt-in local OAuth callback; no undocumented broker endpoints or stored 2FA seeds."""
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from urllib.parse import urlencode

from .fyers_readonly import (API_ROOT, FyersDataError, new_state, validate_app_id,
                             _valid_secret, exchange_code, fetch_cash_quotes)

CALLBACK = "http://127.0.0.1:8502/"
TTL = 600
_LOCK = RLock()
_PENDING = {}


@dataclass
class Pending:
    app_id: str
    secret: str = field(repr=False)
    vault: object = field(repr=False)
    until: float = 0


def _prune(now):
    for key in list(_PENDING):
        if _PENDING[key].until <= now:
            del _PENDING[key]


def prepare(credentials, vault=None, previous=None):
    """Only a local single-user server may call this. Reuse a still-pending link."""
    app_id, secret = validate_app_id(credentials[0]), _valid_secret(credentials[1])
    with _LOCK:
        now = monotonic()
        _prune(now)
        p = _PENDING.get(previous)
        if not p or p.app_id != app_id or p.secret != secret or p.vault is not vault:
            if previous:
                _PENDING.pop(previous, None)
            if len(_PENDING) >= 8:
                raise FyersDataError("Too many pending login requests; restart CCS or retry later.")
            previous = new_state()
            _PENDING[previous] = Pending(app_id, secret, vault, now + TTL)
    return previous, API_ROOT + "/api/v3/generate-authcode?" + urlencode({
        "client_id": app_id, "redirect_uri": CALLBACK, "response_type": "code",
        "state": previous,
    })


def cancel(state):
    with _LOCK:
        _PENDING.pop(state, None)


def consume(states, codes, *, windows_credentials=None, allow=True):
    """Single-use state, expiry and credential binding; verify a real read-only quote."""
    if not allow or len(states) != 1 or not isinstance(states[0], str):
        raise FyersDataError("Invalid or non-local FYERS return. Start a new login.")
    with _LOCK:
        _prune(monotonic())
        p = _PENDING.pop(states[0], None)
    if p is None or len(codes) != 1 or not isinstance(codes[0], str) or not 10 <= len(codes[0]) <= 4096:
        raise FyersDataError("Expired, invalid or already-used FYERS login. Start again.")
    credentials = p.vault.credentials() if p.vault is not None else windows_credentials
    if credentials != (p.app_id, p.secret):
        raise FyersDataError("Saved app changed. Start a new FYERS login.")
    access = exchange_code(p.app_id, p.secret, codes[0])
    if "TCS" not in fetch_cash_quotes(access, ["TCS"]):
        raise FyersDataError("FYERS token received but market-data verification failed. Sign in again.")
    return access, p.vault, p.secret
