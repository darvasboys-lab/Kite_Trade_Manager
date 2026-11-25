"""Optional Windows-only Dhan TOTP access-token manager for local read-only UI.

The user explicitly opts in and supplies their *own* Dhan 6-digit PIN and
TOTP setup seed only through the local Streamlit UI. Credentials and tokens are
held in Windows Credential Manager, NOT in GitHub, .env, cache JSON or logs.

This module touches only Dhan authentication/profile APIs, never trade/order
endpoints, other VMs, or ResultBot's credentials.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import hashlib
import hmac
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SERVICE = "DhanPayoffStudio.LocalTOTP.v1"
AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
PROFILE_URL = "https://api.dhan.co/v2/profile"
TOKEN_MIN_REMAINING = timedelta(minutes=25)
FAILURE_BACKOFF_SECONDS = 600
INDIA_ZONE = timezone(timedelta(hours=5, minutes=30))  # IST needs no Windows tzdata
LOCK = threading.RLock()


class AutoAuthError(ValueError):
    """Safe-to-display error message; must never contain credentials."""


@dataclass(frozen=True)
class AutoAccess:
    token: str
    expires: datetime
    source: str

    def usable(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.expires > now + TOKEN_MIN_REMAINING


def _keyring():
    if sys.platform != "win32":
        raise AutoAuthError(
            "Secure TOTP storage is enabled only on Windows Credential Manager. "
            "Use manual token mode on other systems."
        )
    import keyring

    backend = keyring.get_keyring()
    # Fail closed rather than silently storing broker PIN/TOTP in plaintext.
    module = type(backend).__module__.lower()
    if "windows" not in module:
        raise AutoAuthError(
            "Windows Credential Manager is unavailable; refusing to store broker secrets"
        )
    return keyring


def normalize_totp_secret(secret: str) -> str:
    """Accept 16–64 base32 characters, optional whitespace/spacing."""
    if not isinstance(secret, str):
        raise AutoAuthError("TOTP setup key is invalid")
    key = re.sub(r"[\s-]+", "", secret).upper()
    if not 16 <= len(key) <= 64 or not re.fullmatch(r"[A-Z2-7]+", key):
        raise AutoAuthError(
            "Enter the 16–64 character base32 TOTP setup secret, "
            "not the current 6-digit code or your Dhan access token."
        )
    try:
        raw = base64.b32decode(key + "=" * (-len(key) % 8), casefold=True)
    except Exception as exc:
        raise AutoAuthError("TOTP setup secret is not valid base32") from exc
    if len(raw) < 10:
        raise AutoAuthError("TOTP setup secret is too short")
    return key


def validate_pin(pin: str) -> str:
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6}", pin):
        raise AutoAuthError("Dhan PIN must be exactly 6 numeric digits")
    return pin


def validate_client_id(cid: str) -> str:
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9]{8,15}", cid.strip()):
        raise AutoAuthError("Enter a valid numeric Dhan Client ID")
    return cid.strip()


def _identity(cid: str, suffix: str) -> str:
    return f"{validate_client_id(cid)}:{suffix}"


def is_configured(cid: str) -> bool:
    try:
        ring = _keyring()
        raw = ring.get_password(SERVICE, _identity(cid, "credentials"))
        if not raw:
            return False
        payload = json.loads(raw)
        validate_pin(payload["pin"])
        normalize_totp_secret(payload["totp_secret"])
        return True
    except (AutoAuthError, ValueError, KeyError, TypeError, OSError):
        return False
    except Exception:
        return False


def store_credentials(cid: str, pin: str, totp_secret: str) -> None:
    cid, pin, totp = validate_client_id(cid), validate_pin(pin), normalize_totp_secret(totp_secret)
    ring = _keyring()
    try:
        with LOCK:
            ring.set_password(
                SERVICE, _identity(cid, "credentials"),
                json.dumps({"version": 1, "pin": pin, "totp_secret": totp}, separators=(",", ":")),
            )
    except Exception as exc:
        raise AutoAuthError("Windows Credential Manager could not store the authentication settings") from exc


def forget_credentials(cid: str) -> None:
    ring = _keyring()
    with LOCK:
        for suffix in ("credentials", "token"):
            try:
                ring.delete_password(SERVICE, _identity(cid, suffix))
            except Exception:
                pass


def _load_credentials(cid: str) -> tuple[str, str]:
    ring = _keyring()
    try:
        data = ring.get_password(SERVICE, _identity(cid, "credentials"))
        payload = json.loads(data) if data else None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise AutoAuthError("Auto-login is not configured on this Windows account")
        return validate_pin(payload["pin"]), normalize_totp_secret(payload["totp_secret"])
    except AutoAuthError:
        raise
    except Exception as exc:
        raise AutoAuthError("Secure Dhan TOTP settings could not be read") from exc


def generate_totp(seed: str, at: int | None = None) -> str:
    """Same RFC 6238 standard-library algorithm as ResultBot's compute_totp."""
    raw = base64.b32decode(
        normalize_totp_secret(seed) + "=" * (-len(normalize_totp_secret(seed)) % 8),
        casefold=True,
    )
    counter = int(at if at is not None else time.time()) // 30
    digest = hmac.new(raw, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    truncated = struct.unpack(">I", digest[offset: offset + 4])[0] & 0x7fffffff
    return str(truncated % 1000000).zfill(6)


def _parse_expiry(raw) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise AutoAuthError("Dhan did not provide token expiry")
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Dhan's API says expiryTime is expressed in IST.
            parsed = parsed.replace(tzinfo=INDIA_ZONE)
        parsed = parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise AutoAuthError("Dhan returned an invalid token expiry timestamp") from exc
    now = datetime.now(timezone.utc)
    if not now < parsed <= now + timedelta(hours=26):
        raise AutoAuthError("Dhan returned a token expiry outside the expected 24-hour window")
    return parsed


def _token_error_category(payload: dict) -> str:
    """Static, non-sensitive diagnosis for HTTP-200 broker error envelopes.

    Never echo a returned errorMessage: upstream services may reflect query
    parameters including a PIN or TOTP and we must not expose those values.
    """
    code = payload.get("errorCode") or payload.get("error_code") or ""
    message = payload.get("errorMessage") or payload.get("message") or ""
    value = (str(code) + " " + str(message)).lower()[:512]
    if "totp" in value or "otp" in value or "authenticator" in value:
        return "Dhan rejected the TOTP code; check saved permanent seed and PC clock"
    if "pin" in value or "password" in value:
        return "Dhan rejected the PIN; confirm the saved six-digit Dhan PIN"
    if "client" in value or "account" in value or "ucc" in value:
        return "Dhan rejected the Client ID or account login credentials"
    if "rate" in value or "too many" in value or "limit" in value:
        return "Dhan authentication is rate limited; avoid repeated retries"
    if "expired" in value or "disabled" in value or "not enabled" in value:
        return "Dhan rejected authentication; verify that TOTP is enabled in Dhan Web"
    return "Dhan returned an unsuccessful token-generation response (HTTP 200)"


def _parse_token_response(payload, cid: str, *,
                          allow_missing_client_id: bool = False) -> AutoAccess:
    """Strictly check an issued token, separating missing ID from mismatch.

    Only the caller that immediately verifies GET /v2/profile may permit a
    missing Client ID in an otherwise valid-looking token response. The
    profile must match the configured account before the token is cached.
    """
    if not isinstance(payload, dict):
        raise AutoAuthError(
            "Dhan token-generation response was not a JSON object"
        )
    # Official Dhan docs specify a flat response. Allow a future 'data'
    # envelope only when its token is actually present and still verified
    # by the independent Profile API before it can be stored/used.
    candidate = payload
    inner = payload.get("data")
    if (isinstance(inner, dict) and
            isinstance(inner.get("accessToken"), str)):
        candidate = inner
    expected = str(cid)
    for item in (payload, candidate):
        returned_id = item.get("dhanClientId")
        if returned_id is not None and str(returned_id).strip() != expected:
            raise AutoAuthError(
                "Dhan returned a different Client ID than the one saved "
                "in Settings. No token was accepted."
            )
    raw_token = candidate.get("accessToken")
    if not isinstance(raw_token, str) or not 30 <= len(raw_token) <= 8192:
        # An HTTP-200 error body should not masquerade as an ID mismatch.
        raise AutoAuthError(_token_error_category(candidate))
    if (candidate.get("dhanClientId") is None
            and payload.get("dhanClientId") is None
            and not allow_missing_client_id):
        raise AutoAuthError(
            "Dhan token response omitted Client ID; independent profile "
            "verification is required"
        )
    expiry = _parse_expiry(candidate.get("expiryTime"))
    return AutoAccess(raw_token, expiry, "Dhan TOTP")


def _verify_profile(session, token: str, cid: str) -> None:
    """Check account binding and entitlement before exposing token to quotes."""
    try:
        response = session.get(
            PROFILE_URL, headers={"access-token": token, "Accept": "application/json"},
            timeout=(6, 12), allow_redirects=False,
        )
        if response.status_code != 200:
            raise AutoAuthError(
                f"Dhan token profile validation failed (HTTP {response.status_code})"
            )
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("dhanClientId", "")) != cid:
            raise AutoAuthError("Dhan profile account does not match saved Client ID")
        # Token authentication and Data API entitlement are different.
        # Keep the valid account-bound token so funds can be read even if
        # F&O Data API is inactive; the read-only UI checks dataPlan before
        # ever starting an option-chain scan.
    except AutoAuthError:
        raise
    except Exception as exc:
        raise AutoAuthError("Could not verify the issued token against Dhan profile") from exc


def request_new_access(cid: str, pin: str, secret: str, session=None) -> AutoAccess:
    """Issue one direct TOTP login over TLS, then verify identity/read-only.

    Dhan officially requires a POST with query parameters for this endpoint.
    Never call raise_for_status() on this URL; it could leak PIN/TOTP into logs.
    """
    import requests
    cid, pin = validate_client_id(cid), validate_pin(pin)
    otp = generate_totp(secret)
    session = session or requests
    try:
        response = session.post(
            AUTH_URL,
            params={"dhanClientId": cid, "pin": pin, "totp": otp},
            headers={"Accept": "application/json"},
            timeout=(6, 15), allow_redirects=False,
        )
        if response.status_code != 200:
            raise AutoAuthError(
                f"Dhan TOTP authentication failed (HTTP {response.status_code}); "
                "check PIN, setup secret and device clock"
            )
        obj = response.json()
    except AutoAuthError:
        raise
    except Exception as exc:
        # No raw request URL, response body, PIN or OTP is included.
        raise AutoAuthError("Dhan TOTP authentication could not complete securely") from exc
    # A genuine token that omits dhanClientId (or uses a data envelope)
    # is accepted ONLY after a separate GET /profile confirms same client.
    # An explicit mismatched ID always fails closed before that request.
    result = _parse_token_response(obj, cid, allow_missing_client_id=True)
    _verify_profile(session, result.token, cid)
    return result


def _save_token(cid: str, access: AutoAccess) -> None:
    ring = _keyring()
    try:
        ring.set_password(
            SERVICE, _identity(cid, "token"),
            json.dumps({
                "version": 1, "token": access.token, "expires": access.expires.isoformat(),
            }, separators=(",", ":")),
        )
    except Exception as exc:
        raise AutoAuthError("Windows Credential Manager could not save the new access token") from exc


def read_cached_token(cid: str) -> AutoAccess | None:
    try:
        ring = _keyring()
        raw = ring.get_password(SERVICE, _identity(cid, "token"))
        if not raw:
            return None
        obj = json.loads(raw)
        if obj.get("version") != 1:
            return None
        expiry = datetime.fromisoformat(obj["expires"])
        result = AutoAccess(obj["token"], expiry, "Windows Credential Manager")
        return result if result.usable() else None
    except Exception:
        return None


def load_or_generate(cid: str) -> AutoAccess:
    """Use still-valid token; otherwise login once. Call from a worker only."""
    cid = validate_client_id(cid)
    with LOCK:
        cached = read_cached_token(cid)
        if cached:
            return cached
        pin, seed = _load_credentials(cid)
        result = request_new_access(cid, pin, seed)
        _save_token(cid, result)
        return result
