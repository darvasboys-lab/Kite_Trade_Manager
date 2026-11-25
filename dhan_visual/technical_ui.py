"""Non-blocking Streamlit wiring for paced Yahoo EMA and monthly-R1 columns."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from queue import SimpleQueue
from threading import Event
import time

import streamlit as st

from .technical_indicators import TechnicalLevels, format_level
from .technical_worker import (
    _today_ist, load_all_technicals, read_cached_technicals,
)


# Reveal only one technical category at a time, after all Yahoo history
# batches have finished. The Yahoo price history is fetched ONCE per stock.
TECHNICAL_STAGES = (
    ("EMA 200", "ema200"),
    ("EMA 50", "ema50"),
    ("EMA 20", "ema20"),
    ("EMA 10", "ema10"),
    ("Monthly R1", "monthly_r1"),
)
STAGE_REVEAL_INTERVAL_SECONDS = 3.0


def advance_technical_stage(snap: dict, now: float) -> bool:
    """Advance exactly one column on its scheduled time, never skip stages."""
    if snap.get("status") != "revealing":
        return False
    stage = max(0, min(int(snap.get("stage", 0)), len(TECHNICAL_STAGES)-1))
    if stage >= len(TECHNICAL_STAGES) - 1:
        snap["status"] = "complete"
        return False
    if now < snap.get("stage_next_at", float("inf")):
        return False
    snap["stage"] = stage + 1
    snap["stage_next_at"] = now + STAGE_REVEAL_INTERVAL_SECONDS
    if snap["stage"] == len(TECHNICAL_STAGES) - 1:
        snap["status"] = "complete"
    return True


@st.cache_resource
def _tech_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="yahoo-daily-ema")


def _tech_key(symbols: list[str]) -> str:
    day = _today_ist()
    provider = "FYERS" if st.session_state.get("fno_price_provider") == "FYERS" else "Yahoo"
    raw = f"{day.year:04d}-{day.month:02d}|{provider}|" + "|".join(sorted(set(symbols)))
    return sha256(raw.encode()).hexdigest()


def _get_snapshot(symbols: list[str]) -> dict:
    key = _tech_key(symbols)
    snap = st.session_state.get("fno_technicals")
    if not isinstance(snap, dict) or snap.get("key") != key:
        cached = read_cached_technicals(today=_today_ist())
        available = {s: cached[s] for s in symbols if s in cached}
        snap = {
            "key": key, "values": available, "processed": len(available),
            "total": len(symbols), "failed": 0, "status": "queued",
            # EMA200 appears first, others unlock one-by-one after download.
            "stage": 0, "stage_next_at": float("inf"),
        }
        st.session_state["fno_technicals"] = snap
    return snap


@st.fragment(run_every="3s")
def _tech_heartbeat(symbols, priority_quotes=None, fyers_access=None):
    """Poll worker using Streamlit main thread; never call Streamlit in worker."""
    snap = _get_snapshot(symbols)
    key = snap["key"]
    job = st.session_state.get("fno_technical_job")
    if job and job.get("key") != key and job.get("stop"):
        # A source/expiry change invalidates the old job. Cancel safely
        # before launching a new read-only FYERS or Yahoo history pass.
        job["stop"].set()
    changed = False
    if job and job.get("key") == key:
        queue = job["queue"]
        while not queue.empty():
            batch = queue.get_nowait()
            snap["values"].update(batch.records)
            snap["processed"] = max(snap["processed"], batch.completed)
            snap["failed"] += len(batch.failed)
            changed = True
        if job["future"].done():
            try:
                processed, _ = job["future"].result()
                snap["processed"] = max(snap["processed"], processed)
                snap["status"] = "revealing"
            except Exception:
                # Even on a partial Yahoo failure, reveal whatever legitimate
                # values succeeded in earlier batches without inventing data.
                snap["status"] = "revealing"
            snap["stage_next_at"] = time.monotonic() + STAGE_REVEAL_INTERVAL_SECONDS
            st.session_state.pop("fno_technical_job", None)
            changed = True
    if advance_technical_stage(snap, time.monotonic()):
        changed = True
    if changed:
        st.rerun(scope="app")

    yahoo_job = st.session_state.get("fno_yahoo_job")
    yahoo_snapshot = st.session_state.get("fno_yahoo_snapshot")
    source_ready = bool(fyers_access) or (not yahoo_job and bool(yahoo_snapshot))
    if (not job and source_ready
            and st.session_state.get("fno_technical_attempt") != key):
        # FYERS history is independent of Yahoo; Yahoo requests still wait
        # for its 15-minute cash quote loader to finish.
        st.session_state["fno_technical_attempt"] = key
        queue = SimpleQueue()
        stop = Event()
        job = {
            "key": key, "queue": queue, "stop": stop,
            "future": _tech_pool().submit(
                load_all_technicals, list(symbols), queue, stop,
                priority_quotes=priority_quotes, fyers_access=fyers_access,
            ),
        }
        st.session_state["fno_technical_job"] = job
        snap["status"] = "loading"
    if job and job.get("key") == key:
        st.caption(
            f"{'FYERS' if fyers_access else 'Yahoo'} indicators · EMA 200 first · "
            f"{snap['processed']}/{snap['total']} checked"
        )
    elif snap.get("status") == "revealing":
        current = TECHNICAL_STAGES[snap.get("stage", 0)][0]
        st.caption(f"Indicator stages · {current} ready · revealing next in order")


def technical_values_for_table(symbols: list[str], priority_quotes=None,
                               fyers_access=None) -> dict[str, TechnicalLevels]:
    """Return available technical levels without blocking app rendering."""
    snap = _get_snapshot(symbols)
    _tech_heartbeat(symbols, priority_quotes, fyers_access)
    return snap["values"]


def technical_visible_stage(symbols: list[str]) -> int:
    """The currently unlocked index, from EMA200 to monthly R1."""
    snap = _get_snapshot(symbols)
    return max(0, min(int(snap.get("stage", 0)), len(TECHNICAL_STAGES)-1))


def attach_technical_columns(frame, levels: dict[str, TechnicalLevels],
                             *, stage: int = 4):
    """Keep each indicator value and above-CMP status in one compact cell."""
    if "Symbol" not in frame.columns:
        return frame
    for index, (header, attr) in enumerate(TECHNICAL_STAGES):
        # Headings always exist to avoid a jumping table. An unrevealed
        # stage stays blank even if the values are already in local cache.
        if index > stage:
            frame[header] = ["—"] * len(frame)
        else:
            frame[header] = [
                format_level(
                    getattr(levels[symbol], attr, None) if symbol in levels else None,
                    cmp,
                )
                for symbol, cmp in zip(frame["Symbol"], frame["Last price (₹)"])
            ]
    return frame
