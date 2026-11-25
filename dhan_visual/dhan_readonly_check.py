"""Explicit read-only DhanHQ profile check; never touches orders or tokens.

The user clicks Check before the single-stock option-chain test. Dhan v2
GET /profile reports dataPlan, dataValidity, and tokenValidity. Do not
print client ID, token, response body or authentication secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

import requests

PROFILE_URL = "https://api.dhan.co/v2/profile"


class DhanProfileError(ValueError):
    """Read-only Dhan account readiness cannot be verified."""


@dataclass(frozen=True)
class DhanProfileStatus:
    data_plan: str
    data_validity: str
    token_validity: str
    derivatives_enabled: bool | None
    checked_at: str

    @property
    def data_ready(self) -> bool:
        return self.data_plan.lower() == "active"


def check_dhan_data_plan(client_id: str, token: str, *, session=None) -> DhanProfileStatus:
    """Make ONE GET request and validate its response without side effects."""
    if not re.fullmatch(r"[0-9]{8,15}", str(client_id).strip()):
        raise DhanProfileError("Enter a valid Dhan Client ID")
    if not isinstance(token, str) or len(token.strip()) < 16:
        raise DhanProfileError("Enter a current Dhan access token")
    getter = session or requests
    try:
        response = getter.get(
            PROFILE_URL,
            headers={
                "access-token": token.strip(),
                "Accept": "application/json",
            },
            timeout=(5, 12),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise DhanProfileError(
            f"Dhan profile connection failed ({type(exc).__name__})"
        ) from None
    if response.status_code in (401, 403):
        raise DhanProfileError(
            f"Dhan profile HTTP {response.status_code}: token invalid, expired or unauthorized"
        )
    if response.status_code != 200:
        raise DhanProfileError(
            f"Dhan profile request failed (HTTP {response.status_code})"
        )
    try:
        payload = response.json()
    except ValueError:
        raise DhanProfileError("Dhan profile returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise DhanProfileError("Dhan profile response is not an object")
    returned_id = str(payload.get("dhanClientId", "")).strip()
    if returned_id != str(client_id).strip():
        raise DhanProfileError("Dhan profile client ID does not match this local account")
    plan = str(payload.get("dataPlan") or "Unknown")
    segment = str(payload.get("activeSegment") or "").lower()
    derivatives_enabled = None if not segment else (
        "derivative" in segment or "f&o" in segment
    )
    return DhanProfileStatus(
        data_plan=plan,
        data_validity=str(payload.get("dataValidity") or "Not provided"),
        token_validity=str(payload.get("tokenValidity") or "Not provided"),
        derivatives_enabled=derivatives_enabled,
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
