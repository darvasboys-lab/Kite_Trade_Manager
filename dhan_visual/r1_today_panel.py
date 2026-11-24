"""Compact top-of-screener panel: verified NSE monthly-R1 crosses TODAY.

Starts one paced Yahoo 15-minute background scan only AFTER the existing
daily-history EMA/R1 scan and sequential R1 reveal finish. The full selected
F&O universe is included, irrespective of stock search/contract-price filters.

No order calls, Streamlit worker access, credentials, repeated auto-retries,
or guessing crossing from a single CMP>R1 snapshot.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from queue import SimpleQueue
from threading import Event

import pandas as pd
import streamlit as st

from .r1_today import IST, R1TodayCross, scan_r1_today
from .screener import Quote, screen_rows
from .straddle_panel import available_otm_calls
from .table_layout import autofit_column_config, autofit_enabled, dataframe_key
from .technical_ui import attach_technical_columns
from .tradingview_links import clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY


@st.cache_resource
def _r1_pool():
    # A single background Yahoo scan per running Python process at a time.
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="yahoo-r1-today")


def _r1_key(available, expiry: str, levels, day) -> str:
    # An actual change in monthly R1 or F&O membership must invalidate a
    # stale scan. Do not include the user's current search/price filter.
    parts = sorted(
        f"{item.entry.symbol}:{item.entry.lots[expiry]}:"
        f"{getattr(levels.get(item.entry.symbol), 'monthly_r1', None)}"
        for item in available if expiry in item.entry.lots
    )
    return sha256(
        (day.isoformat() + "|" + expiry + "|" + "|".join(parts)).encode()
    ).hexdigest()


def crossed_r1_frame(available, expiry: str,
                     crossed: dict[str, R1TodayCross],
                     technicals, otm_calls, *, compact: bool) -> pd.DataFrame:
    """Full financial/technical row for EVERY verified R1 breakout today.

    Quote reference is the last verified intraday Yahoo 15m Close from the
    same session that supplied the actual crossing evidence, never a
    potentially delayed Dhan CMP or a differently timed Yahoo cache.
    """
    if not crossed:
        return pd.DataFrame()
    active = [item.entry for item in available if expiry in item.entry.lots]
    quotes = {
        sym: Quote(event.latest_close, event.latest_bar_time + " IST",
                   "Yahoo NSE 15m breakout check")
        for sym, event in crossed.items()
    }
    rows = [
        row for row in screen_rows(
            active, expiry, quotes, min_value=0, max_value=0,
        ) if row["Symbol"] in crossed
    ]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).drop(
        columns=["Company", "Quote time", "Price source"], errors="ignore",
    )
    if compact:
        frame["Contract value (₹L)"] = frame["Contract value (₹)"] / 100000
        frame = frame.drop(columns=["Contract value (₹)"])
    frame = attach_technical_columns(frame, technicals, stage=4)
    frame["5%+ CE premium (%)"] = [
        float(otm_calls[sym].premium_pct) if sym in otm_calls else None
        for sym in frame["Symbol"]
    ]
    frame["R1 crossed at"] = [
        crossed[sym].first_bar_time for sym in frame["Symbol"]
    ]
    frame["Now above R1?"] = [
        "✅ Above" if crossed[sym].currently_above else "Back below"
        for sym in frame["Symbol"]
    ]
    frame["Vs R1 (%)"] = [
        float(crossed[sym].pct_vs_r1) for sym in frame["Symbol"]
    ]
    # Show still-above first, followed by relative percentage (descending).
    frame["_above"] = [
        crossed[sym].currently_above for sym in frame["Symbol"]
    ]
    frame = frame.sort_values(
        ["_above", "Vs R1 (%)", "Symbol"],
        ascending=[False, False, True],
    ).drop(columns=["_above"]).reset_index(drop=True)
    return frame


def _drain(job, snap):
    changed = False
    queue = job["queue"]
    while not queue.empty():
        event = queue.get_nowait()
        snap["crossed"].update(event.records)
        snap["checked"] = max(snap["checked"], event.scanned)
        snap["unknown"] += event.unknown
        snap["warnings"].extend(event.warnings[:2])
        snap["warnings"] = snap["warnings"][-8:]
        changed = True
    if job["future"].done():
        try:
            checked, _ = job["future"].result()
            snap["checked"] = max(snap["checked"], checked)
            snap["status"] = "complete" if snap["checked"] >= snap["total"] else "partial"
        except Exception as exc:
            # Do not show URLs/API response bodies that could contain data.
            snap["status"] = f"error:{type(exc).__name__}"
        st.session_state.pop("ccs_r1_today_job", None)
        changed = True
    return changed


@st.fragment(run_every="5s")
def render_r1_today_panel(available, expiry: str, technicals,
                          compact: bool, technically_ready: bool):
    today = datetime.now(IST).date()
    key = _r1_key(available, expiry, technicals, today)
    history = st.session_state.setdefault("ccs_r1_today_snapshots", {})
    snap = history.get(key)
    if snap is None:
        snap = {
            "key": key, "day": today, "crossed": {}, "checked": 0,
            "total": len(available), "unknown": 0, "warnings": [],
            "status": "waiting",
        }
        history[key] = snap
    job = st.session_state.get("ccs_r1_today_job")
    if job is not None and job["key"] == key:
        _drain(job, snap)
        job = st.session_state.get("ccs_r1_today_job")
    active = job is not None and not job["future"].done()
    another_active = bool(active and job["key"] != key)

    # A date/month rollover makes an old scan ineligible; don't start two.
    symbols = [item.entry.symbol for item in available if expiry in item.entry.lots]
    if technically_ready and not active and snap["status"] == "waiting":
        queue, stop = SimpleQueue(), Event()
        r1_values = {
            sym: technicals[sym].monthly_r1
            for sym in symbols if sym in technicals
        }
        snap["status"] = "scanning"
        st.session_state["ccs_r1_today_job"] = {
            "key": key, "queue": queue, "stop": stop,
            "future": _r1_pool().submit(
                scan_r1_today, symbols, r1_values, queue, stop,
                today=today,
            ),
        }
        job = st.session_state["ccs_r1_today_job"]
        active = True
        another_active = False

    with st.container(border=True):
        heading, control = st.columns([4.2, 1.0], gap="small")
        with heading:
            st.markdown("#### <escape>↗</escape> Crossed Monthly R1 Today")
            st.caption(
                f"{today.strftime('%d %b')} · "
                "verified upward 15m NSE high through current-month R1 · "
                "includes previous-close-confirmed opening gap-ups"
            )
        with control:
            # Manual refresh is offered only after prior scan stops. No
            # recurring polling requests or 219-symbol bursts on reruns.
            refresh = st.button(
                "↻ Recheck", key="ccs_r1_today_refresh",
                disabled=active or not technically_ready,
                help="Run another paced, full-universe 15-minute Yahoo check.",
                use_container_width=True,
            )
        if refresh:
            snap.update(
                crossed={}, checked=0, unknown=0, warnings=[], status="waiting",
            )
            st.rerun(scope="fragment")
        if not technically_ready:
            st.caption(
                "Preparing monthly R1 levels from the paced Yahoo daily-history "
                "scan; the crossover check begins automatically after R1 is ready."
            )
        else:
            st.caption(
                f"**{len(snap['crossed'])} crossed** · "
                f"{snap['checked']}/{snap['total']} stocks checked · "
                f"{snap['unknown']} unavailable "
                + ("· scanning gradually" if active else
                   "· check complete" if snap["status"] == "complete"
                   else "· incomplete")
            )
            if active:
                st.progress(
                    min(1., snap["checked"] / max(1, snap["total"])),
                    text="Checking NSE 15m history in paced Yahoo batches…",
                )
            if another_active:
                st.caption("An earlier R1 scan is still running; results will be scoped to today's date.")
            if snap["status"].startswith("error:"):
                st.warning("Yahoo R1 scan was interrupted; partial confirmed results remain visible.")

            if snap["crossed"]:
                df = crossed_r1_frame(
                    available, expiry, snap["crossed"], technicals,
                    available_otm_calls(available, expiry),
                    compact=compact,
                )
                if not df.empty:
                    display = clickable_stock_frame(
                        df, source="Symbol", target="TradingView chart",
                    )
                    config = {
                        "Symbol": None,
                        "TradingView chart": st.column_config.LinkColumn(
                            "Stock", display_text=TRADINGVIEW_SYMBOL_DISPLAY,
                            help="Open NSE stock chart in TradingView.", width="small",
                        ),
                        "Last price (₹)": st.column_config.NumberColumn(
                            "Yahoo 15m CMP", format="₹%.2f",
                        ),
                        "Lot size": st.column_config.NumberColumn(
                            "Lot", format="%d",
                        ),
                        "R1 crossed at": st.column_config.TextColumn(
                            "Cross candle", help="Start time of 15m candle, IST",
                        ),
                        "Vs R1 (%)": st.column_config.NumberColumn(
                            "Vs R1", format="%.2f%%",
                        ),
                        "5%+ CE premium (%)": st.column_config.NumberColumn(
                            "CE ≥+5%", format="%.2f%%",
                        ),
                    }
                    if compact:
                        config["Contract value (₹L)"] = st.column_config.NumberColumn(
                            "Value (₹L)", format="₹%.1fL",
                        )
                    else:
                        config["Contract value (₹)"] = st.column_config.NumberColumn(
                            "Value (₹)", format="₹%.0f",
                        )
                    for key_name in ("EMA 200", "EMA 50", "EMA 20",
                                     "EMA 10", "Monthly R1"):
                        config[key_name] = st.column_config.TextColumn(
                            key_name, width="medium",
                        )
                    st.dataframe(
                        display, hide_index=True, use_container_width=True,
                        height=min(570, 90 + 35 * len(df)),
                        key=dataframe_key("ccs_r1_today_crossed", st.session_state),
                        column_config=autofit_column_config(
                            config, autofit_enabled(st.session_state),
                        ),
                    )
                    st.download_button(
                        "Export today's R1 crossings (CSV)",
                        data=df.to_csv(index=False),
                        file_name=f"r1_crossings_{today.isoformat()}.csv",
                        mime="text/csv", key="ccs_r1_today_csv",
                    )
            elif snap["status"] == "complete" and snap["unknown"] == 0:
                st.caption("No verified upward monthly R1 crossings in today's checked stocks.")
            elif snap["status"] == "complete":
                st.caption(
                    "No confirmed crossings in available Yahoo data. Some "
                    "stocks have insufficient intraday/R1 evidence."
                )
            with st.expander("R1 crossover method and incomplete coverage", expanded=False):
                st.caption(
                    "The current month's standard R1 uses the previous completed month's "
                    "high/low/close. A stock qualifies when its NSE 15m high exceeds R1 "
                    "after a verified at/below open or preceding close today. An "
                    "opening gap requires the prior completed session close at/below R1. "
                    "Stocks already above R1 yesterday, with no new upward crossing, "
                    "are excluded."
                )
                st.caption(
                    "If the price later drops below R1, the stock remains in the "
                    "crossed-today list and is marked Back below. Yahoo intraday "
                    "history is delayed/limited and may be unavailable. Cross candle "
                    "shows its 15-minute starting time, not an exact trade timestamp."
                )
                if snap["warnings"]:
                    st.caption(f"Recent missing-data samples: {'; '.join(snap['warnings'][:3])}")
                st.caption(
                    f"Scan date {today.isoformat()} IST · "
                    f"verified {max(0, snap['checked']-snap['unknown'])}"
                    f"/{snap['total']} · unknown {snap['unknown']} · "
                    "no alerts/orders or broker requests"
                )
