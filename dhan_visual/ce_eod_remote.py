"""Best-effort download of an official, already-published NSE F&O UDiFF ZIP.

Only fixed NSE archive host/path, bounded archive bytes and recent dated filenames;
no browser spoofing, login, cookies, scraping, circumvention, brokers or orders.
On 403/429/network errors, fail closed and let the user import the report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

import requests

from .nse_eod_ce import MAX_UPLOAD_BYTES, NseEodError, NseEodSnapshot, parse_nse_fo_eod

IST = timezone(timedelta(hours=5, minutes=30))
ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/fo/"
# A specific published 2026 closure, not a complete holiday calendar.
KNOWN_NSE_HOLIDAYS = frozenset({date(2026, 11, 24)})
MAX_LOOKBACK_DAYS = 12
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class EodDownloadResult:
    snapshot: NseEodSnapshot | None
    source_url: str
    message: str
    checked: tuple[str, ...]
    # Successful response bytes can be safely cached after strict validation.
    # No account, token or broker information is sent to the public archive.
    payload: bytes | None = None


def archive_url(day: date) -> str:
    return (ARCHIVE_BASE + "BhavCopy_NSE_FO_0_0_0_" +
            day.strftime("%Y%m%d") + "_F_0000.csv.zip")


def report_candidates(now: datetime | None = None) -> list[date]:
    """Recent completed NSE sessions, excluding weekends and known holidays."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    local = current.astimezone(IST)
    # Official F&O ZIP may publish after close. Do not request a partial
    # current-day report before 18:00 IST.
    first = local.date() if local.time() >= time(18, 0) else local.date() - timedelta(days=1)
    choices = []
    for offset in range(MAX_LOOKBACK_DAYS):
        day = first - timedelta(days=offset)
        if day.weekday() < 5 and day not in KNOWN_NSE_HOLIDAYS:
            choices.append(day)
            if len(choices) >= MAX_ATTEMPTS:
                break
    return choices


def fetch_published_nse_eod(*, now: datetime | None = None,
                            get: Callable | None = None) -> EodDownloadResult:
    """Retrieve only existing daily archives, one bounded attempt per candidate.

    Any 403 or 429 stops immediately rather than retrying or circumventing
    exchange controls; 404 is expected on holidays and unpublished sessions.
    """
    getter = get or requests.get
    checked = []
    for day in report_candidates(now):
        url = archive_url(day)
        checked.append(day.isoformat())
        try:
            with getter(url, timeout=(4, 12), stream=True, allow_redirects=False) as res:
                if res.status_code == 404:
                    continue
                if res.status_code in (401, 403, 429):
                    return EodDownloadResult(
                        None, "", f"NSE archive access denied (HTTP {res.status_code}); "
                        "download the report manually from NSE.", tuple(checked)
                    )
                if res.status_code != 200:
                    return EodDownloadResult(
                        None, "", f"NSE archive HTTP {res.status_code}; "
                        "use an official downloaded file.", tuple(checked)
                    )
                size_header = res.headers.get("content-length", "")
                if size_header.isdecimal() and int(size_header) > MAX_UPLOAD_BYTES:
                    raise NseEodError("Official ZIP exceeds maximum permitted size")
                data = bytearray()
                for chunk in res.iter_content(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise NseEodError("Official ZIP exceeds maximum permitted size")
                snapshot = parse_nse_fo_eod(bytes(data))
                if snapshot.report_date != day.isoformat():
                    raise NseEodError("NSE archive's report date does not match URL")
                return EodDownloadResult(
                    snapshot, url, f"NSE F&O EOD {snapshot.report_date}",
                    tuple(checked), bytes(data),
                )
        except (requests.RequestException, NseEodError, OSError) as exc:
            return EodDownloadResult(
                None, "", f"NSE archive unavailable ({type(exc).__name__}); "
                "use a locally downloaded NSE ZIP or upload it.", tuple(checked)
            )
    return EodDownloadResult(
        None, "", "No published NSE F&O archive found for recent sessions. "
        "Download one from the NSE reports page.", tuple(checked)
    )
