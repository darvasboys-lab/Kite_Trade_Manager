"""Paced Yahoo daily-history worker and gitignored public-data indicator cache.

No Streamlit calls, Dhan credentials, TOTP, broker orders or file uploads.
Runs after Yahoo cash-price preload to avoid hammering a free data provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from queue import SimpleQueue
from threading import Event
import json
import os
import tempfile
import time

from .technical_indicators import TechnicalLevels, download_history_batch

CACHE_PATH = Path(__file__).resolve().parent.parent / ".dhan_cache" / "yahoo_technical.json"
CACHE_VERSION = 1
CACHE_MAX_AGE = timedelta(hours=9)
BATCH_SIZE = 6
BATCH_DELAY_SECONDS = 6.0
EMPTY_BATCH_BACKOFF_SECONDS = 30.0
INDIA = timezone(timedelta(hours=5, minutes=30))
VALUES = ("ema200", "ema50", "ema20", "ema10", "monthly_r1", "last_daily_close")


@dataclass(frozen=True)
class TechnicalUpdate:
    records: dict[str, TechnicalLevels]
    completed: int
    total: int
    failed: tuple[str, ...] = ()


def _today_ist() -> date:
    return datetime.now(INDIA).date()


def _serialize(data: dict[str, TechnicalLevels], day: date) -> dict:
    return {
        "version": CACHE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "month": f"{day.year:04d}-{day.month:02d}",
        "records": {
            sym: {**{name: str(getattr(levels, name)) if getattr(levels, name) is not None else None
                     for name in VALUES},
                  "asof": levels.asof, "pivot_month": levels.pivot_month}
            for sym, levels in data.items()
        }
    }


def read_cached_technicals(path: Path = CACHE_PATH,
                           *, today: date | None = None,
                           now: datetime | None = None) -> dict[str, TechnicalLevels]:
    """Accept only recent public price-derived levels from the current month."""
    day = today or _today_ist()
    now = now or datetime.now(timezone.utc)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 300_000:
            return {}
        obj = json.loads(path.read_text(encoding="utf-8"))
        if obj.get("version") != CACHE_VERSION or obj.get("month") != day.strftime("%Y-%m"):
            return {}
        stamp = datetime.fromisoformat(obj["updated_at"])
        if (stamp.tzinfo is None or stamp > now or now - stamp > CACHE_MAX_AGE
                or not isinstance(obj.get("records"), dict)):
            return {}
        parsed = {}
        for symbol, item in obj["records"].items():
            if not isinstance(item, dict) or not isinstance(symbol, str):
                continue
            try:
                values = {
                    name: Decimal(item[name]) if item.get(name) is not None else None
                    for name in VALUES
                }
                if any(x is not None and (not x.is_finite() or x <= 0)
                       for x in values.values()):
                    continue
                asof = date.fromisoformat(item["asof"])
                if asof > day or item["pivot_month"] != day.strftime("%Y-%m"):
                    continue
                parsed[symbol] = TechnicalLevels(
                    values["ema200"], values["ema50"], values["ema20"],
                    values["ema10"], values["monthly_r1"], values["last_daily_close"],
                    asof.isoformat(), item["pivot_month"]
                )
            except (TypeError, ValueError, KeyError, InvalidOperation):
                continue
        return parsed
    except (OSError, ValueError, TypeError, UnicodeError, AttributeError):
        return {}


def save_cached_technicals(records: dict[str, TechnicalLevels],
                           day: date, path: Path = CACHE_PATH) -> None:
    """Atomically persist PUBLIC OHLC-derived levels only, never credentials."""
    if path.is_symlink():
        return
    name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=".indicators-", suffix=".tmp", delete=False
        ) as handle:
            name = handle.name
            json.dump(_serialize(records, day), handle, separators=(",", ":"))
        os.replace(name, path)
    except OSError:
        pass  # Loss of optional cache never blocks UI or pricing.
    finally:
        if name and os.path.exists(name):
            try:
                os.unlink(name)
            except OSError:
                pass


def load_all_technicals(symbols: list[str], queue: SimpleQueue,
                        cancel: Event, *,
                        today: date | None = None,
                        batch_size: int = BATCH_SIZE,
                        pause: float = BATCH_DELAY_SECONDS,
                        cache_path: Path = CACHE_PATH) -> tuple[int, int]:
    """Gradually fetch 2-year history for every F&O stock with no user clicks.

    Reuses at most 9-hour-old public cache, groups ≤10 Yahoo symbols, sleeps
    between requests, and backs off on empty/rate-limited responses.
    """
    day = today or _today_ist()
    unique = list(dict.fromkeys(symbols))
    if not 1 <= batch_size <= 10:
        raise ValueError("Yahoo batch size must be 1–10 stocks")
    known = read_cached_technicals(cache_path, today=day)
    cache = {s: known[s] for s in unique if s in known}
    total = len(unique)
    completed = len(cache)
    if cache:
        queue.put(TechnicalUpdate(dict(cache), completed, total))
    remaining = [s for s in unique if s not in cache]
    for start in range(0, len(remaining), batch_size):
        if cancel.is_set():
            break
        group = remaining[start:start + batch_size]
        try:
            output, errors = download_history_batch(group, current_month=day)
        except Exception as exc:
            output, errors = {}, [f"Yahoo history unavailable ({type(exc).__name__})"]
        for symbol, value in output.items():
            if symbol in group:
                cache[symbol] = value
        completed += len(group)
        queue.put(TechnicalUpdate(
            {sym: value for sym, value in output.items() if sym in group},
            completed, total,
            tuple(errors[:5]),
        ))
        if output:
            save_cached_technicals(cache, day, cache_path)
        if start + batch_size < len(remaining):
            delay = EMPTY_BATCH_BACKOFF_SECONDS if not output else pause
            if cancel.wait(delay):
                break
    return completed, total
