"""Local, verified NSE F&O EOD cache for the Covered Call Strategy Builder.

This is a file-import workflow, not NSE scraping or a broker market-data API.
It retains public exchange reports only; no Dhan tokens or account credentials.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import tempfile

from .ce_eod_local import IST, NSE_FO_NAME, recent_nse_fo_reports
from .nse_eod_ce import (
    MAX_UPLOAD_BYTES, NseEodError, NseEodSnapshot, parse_nse_fo_eod,
)

CACHE_FILE = Path(__file__).resolve().parent.parent / ".dhan_cache" / "nse_fo_eod_last.bin"


def _validated_payload(payload: bytes) -> NseEodSnapshot:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_UPLOAD_BYTES:
        raise NseEodError("NSE report must be a ZIP/CSV of at most 16 MB")
    snapshot = parse_nse_fo_eod(payload)
    if snapshot.report_date > datetime.now(IST).date().isoformat():
        raise NseEodError("NSE report has a future business date")
    return snapshot


def read_verified_report(path: Path = CACHE_FILE) -> NseEodSnapshot | None:
    """Read only a small previously validated local report; fail closed."""
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            return None
        if not 0 < path.stat().st_size <= MAX_UPLOAD_BYTES:
            return None
        return _validated_payload(path.read_bytes())
    except (NseEodError, OSError, ValueError):
        return None


def save_verified_report(payload: bytes, path: Path = CACHE_FILE) -> NseEodSnapshot:
    """Validate before atomic local persistence, never overwrite a newer day."""
    incoming = _validated_payload(payload)
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise NseEodError("NSE cache path cannot be a symbolic link")
    existing = read_verified_report(path)
    if existing and existing.report_date > incoming.report_date:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".nse-eod-", suffix=".tmp",
            delete=False,
        ) as output:
            tmp = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    return incoming


def load_local_eod(path: Path = CACHE_FILE, *,
                   downloads: Path | None = None) -> tuple[NseEodSnapshot | None, str]:
    """Use newest validated saved/imported or Downloads report; no network.

    A filename alone cannot establish authenticity: its embedded report date,
    UDiFF fields and contract details must parse successfully.
    """
    best = read_verified_report(path)
    label = "Previously imported NSE F&O report" if best else ""
    for file in recent_nse_fo_reports(folder=downloads):
        match = NSE_FO_NAME.fullmatch(file.name)
        if not match:
            continue
        try:
            payload = file.read_bytes()
            snap = _validated_payload(payload)
            expected = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
            if snap.report_date != expected:
                continue
            if best is None or snap.report_date > best.report_date:
                best = snap
                label = f"Downloads/{file.name}"
                # A fresh browser/app session can reuse this validated report.
                try:
                    save_verified_report(payload, path)
                except (OSError, NseEodError):
                    pass
        except (NseEodError, OSError, ValueError):
            continue
    return best, label


def eod_cash_quotes(snapshot: NseEodSnapshot | None) -> dict:
    """Reference cash closes sourced from the SAME dated F&O report.

    They are deliberately labeled EOD, never represented as current LTP/CMP.
    """
    if snapshot is None:
        return {}
    from .screener import Quote
    return {
        symbol: Quote(price, snapshot.report_date, "NSE F&O EOD underlying close")
        for symbol, price in snapshot.underlyings.items()
        if price > 0
    }
