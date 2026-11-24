"""Find an owner-downloaded NSE FO bhavcopy locally. No network or scraping.

The application reads only appropriately named NSE F&O daily reports in the
current Windows user's Downloads folder. It does not search personal folders,
persist downloaded content or retrieve any URL automatically.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

from .nse_eod_ce import MAX_UPLOAD_BYTES


IST = timezone(timedelta(hours=5, minutes=30))
NSE_FO_NAME = re.compile(
    r"^BhavCopy_NSE_FO_0_0_0_(\d{8})_F_0000\.csv(?:\.zip)?$", re.IGNORECASE
)
MAX_REPORT_AGE_DAYS = 10
MAX_REPORT_CANDIDATES = 5


def recent_nse_fo_reports(
    folder: Path | None = None, *,
    today: date | None = None,
    max_age_days: int = MAX_REPORT_AGE_DAYS,
) -> list[Path]:
    """Newest verified-looking, recent, bounded local reports first.

    Reports are only recognized by filename/size here. Contents are later
    validated by the existing strict UDiFF parser.
    """
    folder = Path(folder) if folder is not None else Path.home() / "Downloads"
    if not folder.is_dir() or folder.is_symlink():
        return []
    day = today or datetime.now(IST).date()
    found: list[tuple[date, Path]] = []
    try:
        for path in folder.iterdir():
            match = NSE_FO_NAME.fullmatch(path.name)
            if not match or path.is_symlink() or not path.is_file():
                continue
            try:
                stamp = datetime.strptime(match.group(1), "%Y%m%d").date()
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            if (stamp > day or day - stamp > timedelta(days=max_age_days)
                    or not 0 < size <= MAX_UPLOAD_BYTES):
                continue
            found.append((stamp, path))
    except OSError:
        return []
    # Prefer the ZIP for a date, and keep a small fixed parsing budget.
    found.sort(key=lambda item: (
        -item[0].toordinal(), not item[1].name.lower().endswith(".zip"),
        item[1].name.lower(),
    ))
    return [path for _, path in found[:MAX_REPORT_CANDIDATES]]
