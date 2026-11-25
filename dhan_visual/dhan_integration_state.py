"""Persistent NONSECRET Dhan integration milestone, plus opt-in Windows token vault.

Verifying Dhan's profile AND an authentic one-stock option chain is a
one-time onboarding milestone. The milestone survives app restarts but must
never be confused with an active access token: Dhan tokens expire within 24h.

No token, PIN, TOTP key or trade credentials are written to files. Optional
manual token storage is only through Windows Credential Manager; no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import tempfile

from .auto_auth import AutoAuthError, _keyring, validate_client_id

IST = timezone(timedelta(hours=5, minutes=30))
STATUS_FILE = Path(__file__).resolve().parent.parent / ".dhan_cache" / "verified_integration.json"
VAULT_SERVICE = "CoveredCallBuilder.DhanManualToken.v1"
STATUS_VERSION = 1


@dataclass(frozen=True)
class VerifiedIntegration:
    client_id: str
    verified_at: str
    symbol: str
    expiry: str
    data_plan: str
    token_expires_at: str


def parse_dhan_token_expiry(raw: str) -> datetime | None:
    """Parse published Dhan Profile tokenValidity, interpreting naive dates IST."""
    if not isinstance(raw, str) or len(raw) > 64:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=IST)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_expiry(raw: str) -> datetime | None:
    try:
        date_obj = datetime.fromisoformat(raw)
        if date_obj.tzinfo is None:
            return None
        return date_obj.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def verified_account(cid: str, path: Path = STATUS_FILE) -> VerifiedIntegration | None:
    """A verified setup remains remembered; this is NOT live authentication."""
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9]{8,15}", cid):
        return None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2048:
            return None
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or obj.get("version") != STATUS_VERSION:
            return None
        if obj.get("client_id") != cid or obj.get("data_plan") != "Active":
            return None
        when = _safe_expiry(obj.get("verified_at"))
        if when is None or when > datetime.now(timezone.utc) + timedelta(minutes=5):
            return None
        symbol = obj.get("symbol")
        expiry = obj.get("expiry")
        if (not isinstance(symbol, str)
                or not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol)
                or not isinstance(expiry, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry)):
            return None
        token_exp = obj.get("token_expires_at", "")
        if token_exp and _safe_expiry(token_exp) is None:
            return None
        return VerifiedIntegration(
            cid, obj["verified_at"], symbol, expiry, "Active", token_exp
        )
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        return None


def record_verification(
    cid: str, symbol: str, expiry: str, token_validity: str,
    *, path: Path = STATUS_FILE, now: datetime | None = None,
) -> VerifiedIntegration:
    """Write proof of successful profile+option chain, never a credential."""
    cid = validate_client_id(cid)
    if not re.fullmatch(r"[A-Z0-9&-]{1,25}", symbol):
        raise ValueError("Invalid verified stock symbol")
    try:
        datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Invalid verified stock expiry") from None
    if path.is_symlink():
        raise OSError("Refusing to store integration status through symlink")
    checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = parse_dhan_token_expiry(token_validity)
    status = VerifiedIntegration(
        cid, checked.isoformat(timespec="seconds"), symbol, expiry,
        "Active", expires.isoformat(timespec="seconds") if expires else "",
    )
    obj = {
        "version": STATUS_VERSION, "client_id": cid,
        "verified_at": status.verified_at, "symbol": symbol, "expiry": expiry,
        "data_plan": "Active", "token_expires_at": status.token_expires_at,
    }
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".verified-",
            suffix=".tmp", dir=path.parent, delete=False
        ) as fp:
            temp = fp.name
            json.dump(obj, fp, separators=(",", ":"))
            fp.write("\n")
        os.replace(temp, path)
    finally:
        if temp:
            try:
                if os.path.exists(temp):
                    os.unlink(temp)
            except OSError:
                pass
    return status


def token_still_current(status: VerifiedIntegration, *,
                        now: datetime | None = None) -> bool:
    """Reported Dhan token validity is not a forever-connection claim."""
    expires = _safe_expiry(status.token_expires_at)
    checked = now or datetime.now(timezone.utc)
    return expires is not None and expires > checked + timedelta(minutes=5)


def save_manual_token(cid: str, token: str, token_validity: str) -> None:
    """Explicit opt-in only, stored in Windows vault, with bounded expiry."""
    cid = validate_client_id(cid)
    if not isinstance(token, str) or not 16 <= len(token) <= 8192:
        raise AutoAuthError("Dhan token is invalid")
    expires = parse_dhan_token_expiry(token_validity)
    now = datetime.now(timezone.utc)
    if expires is None or not now + timedelta(minutes=5) < expires <= now + timedelta(hours=26):
        raise AutoAuthError(
            "Cannot securely remember this token because Dhan expiry is missing or invalid"
        )
    try:
        _keyring().set_password(
            VAULT_SERVICE, cid,
            json.dumps({"version": 1, "expires": expires.isoformat(), "token": token},
                       separators=(",", ":")),
        )
    except AutoAuthError:
        raise
    except Exception:
        raise AutoAuthError("Windows Credential Manager could not save Dhan token") from None


def load_manual_token(cid: str) -> str:
    """Expired tokens are NEVER offered to broker calls."""
    try:
        cid = validate_client_id(cid)
        raw = _keyring().get_password(VAULT_SERVICE, cid)
        if not raw:
            return ""
        obj = json.loads(raw)
        if not isinstance(obj, dict) or obj.get("version") != 1:
            return ""
        expires = _safe_expiry(obj.get("expires"))
        token = obj.get("token")
        if (not expires or expires <= datetime.now(timezone.utc) + timedelta(minutes=5)
                or not isinstance(token, str) or not 16 <= len(token) <= 8192):
            return ""
        return token
    except Exception:
        return ""


def clear_manual_token(cid: str) -> None:
    try:
        _keyring().delete_password(VAULT_SERVICE, validate_client_id(cid))
    except Exception:
        pass
