"""Dhan Funds API: bounded, read-only account balance retrieval.

Official GET https://api.dhan.co/v2/fundlimit. The published field name
is 'availabelBalance' (Dhan spelling). Never log credentials or response
bodies; never call trading/order endpoints. The UI alone controls cadence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

import requests


FUNDS_URL = "https://api.dhan.co/v2/fundlimit"


class DhanFundsError(ValueError):
    """A safe-to-display, non-sensitive balance retrieval error."""


@dataclass(frozen=True)
class FundsSnapshot:
    available: Decimal
    utilized: Decimal | None
    withdrawable: Decimal | None
    checked_at: str


def _money(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        money = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return money if money.is_finite() and money >= 0 else None


def read_dhan_balance(client_id: str, token: str, *, session=None) -> FundsSnapshot:
    """GET only; explicitly check the account ID and required balance field."""
    if not re.fullmatch(r"[0-9]{8,15}", str(client_id).strip()):
        raise DhanFundsError("Invalid Dhan Client ID")
    if not isinstance(token, str) or not 16 <= len(token.strip()) <= 8192:
        raise DhanFundsError("Dhan access token unavailable")
    client = session or requests
    try:
        response = client.get(
            FUNDS_URL,
            headers={
                "access-token": token.strip(),
                "Accept": "application/json",
            },
            timeout=(5, 12), allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise DhanFundsError(
            f"Dhan balance network failure ({type(exc).__name__})"
        ) from None
    if response.status_code in (401, 403):
        raise DhanFundsError("Dhan balance unavailable: token or permission rejected")
    if response.status_code == 429:
        raise DhanFundsError("Dhan balance temporarily rate limited (HTTP 429)")
    if response.status_code != 200:
        raise DhanFundsError(
            f"Dhan balance unavailable (HTTP {response.status_code})"
        )
    try:
        payload = response.json()
    except ValueError:
        raise DhanFundsError("Dhan balance response is invalid") from None
    if not isinstance(payload, dict) or str(payload.get("dhanClientId", "")) != client_id:
        raise DhanFundsError("Dhan balance account verification failed")
    # Do not confuse zero cash with an absent or differently named field.
    balance = _money(payload.get("availabelBalance"))
    if balance is None:
        raise DhanFundsError("Dhan did not return a valid available balance")
    return FundsSnapshot(
        available=balance,
        utilized=_money(payload.get("utilizedAmount")),
        withdrawable=_money(payload.get("withdrawableBalance")),
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
