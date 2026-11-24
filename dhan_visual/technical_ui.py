"""Non-blocking Streamlit wiring for paced Yahoo EMA and monthly-R1 columns."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from queue import SimpleQueue
from threading import Event

import streamlit as st

from .technical_indicators import TechnicalLevels, format_level
from .technical_worker import (
    _today_ist, load_all_technicals, read_cached_technicals,
)


@st.cache_resource
def _tech_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="yahoo-daily-ema")


def _tech_key(symbols: list[str]) -> str:
    day = _today_ist()
    raw = f"{day.year:04d}-{day.month:02d}|" + "|".join(sorted(set(symbols)))
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
        }
        st.session_state["fno_technicals"] = snap
    return snap


@st.fragment(run_every="3s")
def _tech_heartbeat(symbols):
    """Poll worker using Streamlit main thread; never call Streamlit in worker."""
    snap = _get_snapshot(symbols)
    key = snap["key"]
    job = st.session_state.get("fno_technical_job")
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
                snap["status"] = "complete"
            except Exception:
                snap["status"] = "error"
            st.session_state.pop("fno_technical_job", None)
            changed = True
    if changed:
        st.rerun(scope="app")

    yahoo_job = st.session_state.get("fno_yahoo_job")
    yahoo_snapshot = st.session_state.get("fno_yahoo_snapshot")
    if (not job
            and not yahoo_job
            and yahoo_snapshot
            and st.session_state.get("fno_technical_attempt") != key):
        # Never overlap the Yahoo 15-minute quote loader with the history scan.
        st.session_state["fno_technical_attempt"] = key
        queue = SimpleQueue()
        stop = Event()
        job = {
            "key": key, "queue": queue, "stop": stop,
            "future": _tech_pool().submit(
                load_all_technicals, list(symbols), queue, stop,
            ),
        }
        st.session_state["fno_technical_job"] = job
        snap["status"] = "loading"
    if job and job.get("key") == key:
        st.caption(
            f"Yahoo technicals · {snap['processed']}/{snap['total']} checked · "
            f"{len(snap['values'])} usable · slow background download"
        )


def technical_values_for_table(symbols: list[str]) -> dict[str, TechnicalLevels]:
    """Return available technical levels without blocking app rendering."""
    snap = _get_snapshot(symbols)
    _tech_heartbeat(symbols)
    return snap["values"]


def attach_technical_columns(frame, levels: dict[str, TechnicalLevels]):
    """Keep each indicator value and above-CMP status in one compact cell."""
    if "Symbol" not in frame.columns:
        return frame
    cols = [
        ("EMA 200", "ema200"),
        ("EMA 50", "ema50"),
        ("EMA 20", "ema20"),
        ("EMA 10", "ema10"),
        ("Monthly R1", "monthly_r1"),
    ]
    for header, attr in cols:
        frame[header] = [
            format_level(
                getattr(levels[symbol], attr, None) if symbol in levels else None,
                cmp,
            )
            for symbol, cmp in zip(frame["Symbol"], frame["Last price (₹)"])
        ]
    return frame
