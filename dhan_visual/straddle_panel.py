"""Streamlit UI for full-universe, descending ATM short-straddle premium ranking.

This screen never requests 219 option chains automatically on page load.
One explicit Scan All click starts a safe, background, rate-limited pass of
the *entire* selected monthly stock F&O universe; no repeated per-stock clicks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from hashlib import sha256
from queue import SimpleQueue
from threading import Event

import pandas as pd
import streamlit as st

from .auto_auth import is_configured
from .local_client_settings import read_client_id, save_client_id, valid_client_id
from .option_panel import _local_creds
from .straddle_scan import rank_straddles, scan_all_straddles
from .table_layout import autofit_column_config, autofit_enabled, dataframe_key
from .tradingview_links import clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY


@st.cache_resource
def _straddle_pool():
    # Process-wide Dhan request limiter still serializes option-chain HTTP.
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="dhan-straddle-scan")


def available_otm_calls(available, expiry: str) -> dict:
    """Read valid +5% CE snapshots without new option-chain calls."""
    history = st.session_state.get("fno_straddle_snapshots") or {}
    snap = history.get(_scan_key(available, expiry)) or {}
    return snap.get("otm_calls") or {}


def _scan_key(available, expiry: str) -> str:
    symbols = "|".join(sorted(
        f"{x.entry.symbol}:{x.entry.lots[expiry]}"
        for x in available if expiry in x.entry.lots
    ))
    return sha256((expiry + "|" + symbols).encode()).hexdigest()


def straddle_dataframe(rows, otm_calls=None) -> pd.DataFrame:
    """Sort numeric percentages descending; no lexicographic percentage strings."""
    ranked = rank_straddles(rows)
    otm_calls = otm_calls or {}
    return pd.DataFrame([
        {
            "Rank": index,
            "Stock": item.symbol,
            "ATM strike (₹)": float(item.strike),
            "CMP (₹)": float(item.underlying_ltp),
            "CE LTP (₹)": float(item.call_ltp),
            "PE LTP (₹)": float(item.put_ltp),
            "CE+PE (₹)": float(item.combined_ltp),
            "Straddle premium (%)": float(item.premium_pct),
            "Gross credit/lot (₹)": float(item.gross_lot_premium),
            "Bid-based (%)": float(item.combined_bid_pct)
            if item.combined_bid_pct is not None else None,
            "5%+ CE premium (%)": float(otm_calls[item.symbol].premium_pct)
            if item.symbol in otm_calls else None,
            "5%+ CE strike (₹)": float(otm_calls[item.symbol].strike)
            if item.symbol in otm_calls else None,
            "Expiry": item.expiry,
        }
        for index, item in enumerate(ranked, 1)
    ])


def _drain_events(snap):
    job = st.session_state.get("fno_straddle_job")
    if job is None or job["key"] != snap["key"]:
        return False
    changed = False
    queue = job["queue"]
    while not queue.empty():
        event = queue.get_nowait()
        changed = True
        if event.status:
            snap["status"] = event.status
        if event.symbol:
            snap["processed"] = event.completed
            if event.otm_call is not None:
                snap.setdefault("otm_calls", {})[event.symbol] = event.otm_call
            if event.row is not None:
                snap["rows"][event.symbol] = event.row
                snap["errors"].pop(event.symbol, None)
            elif event.error:
                snap["errors"][event.symbol] = event.error
        if event.fatal:
            snap["fatal"] = event.error
    if job["future"].done():
        try:
            processed, total = job["future"].result()
            snap["processed"] = max(processed, snap["processed"])
            if not snap["fatal"]:
                snap["status"] = (
                    "Complete" if snap["processed"] >= total
                    else "Stopped · resume remaining stocks"
                )
        except Exception as exc:
            snap["fatal"] = f"Background scan failed ({type(exc).__name__})"
        st.session_state.pop("fno_straddle_job", None)
        changed = True
    return changed


@st.fragment(run_every="2s")
def render_straddle_panel(available, expiry):
    """Live progress and sorting for the entire expiry universe, one user click."""
    total = len(available)
    key = _scan_key(available, expiry)
    history = st.session_state.setdefault("fno_straddle_snapshots", {})
    snap = history.get(key)
    if snap is None:
        snap = {
            "key": key, "month": expiry, "total": total,
            "rows": {}, "otm_calls": {}, "errors": {}, "processed": 0,
            "status": "", "fatal": "", "started": False,
        }
        history[key] = snap
    changed = _drain_events(snap)
    if changed:
        # A full-script refresh updates the main stock table's OTM column.
        st.rerun(scope="app")
    active = st.session_state.get("fno_straddle_job")
    busy = bool(active and not active["future"].done())
    our_job = busy and active["key"] == key

    st.markdown("#### ATM short-straddle screener")
    st.caption(
        "ATM straddle % = (CE+PE) ÷ Dhan CMP ×100 · "
        "+5% CE % = CE LTP at first strike ≥ CMP×1.05 ÷ CMP ×100 · "
        "one read-only option-chain response per stock."
    )
    with st.container(border=True):
        left, right = st.columns([2.1, 1])
        with left:
            st.markdown(
                f"**All {total} stock F&O underlyings** · "
                f"{len(snap['rows'])} valid straddles"
            )
            if not snap["started"]:
                st.caption(
                    "A single click scans the full universe in the background. "
                    "Dhan limits option-chain requests to approximately one every "
                    "3 seconds; this may take a while. The main Yahoo screener stays usable."
                )
            elif our_job:
                st.caption(
                    f"Scanning {snap['processed']}/{total} · "
                    f"{len(snap['rows'])} ranked · {len(snap['errors'])} unavailable"
                )
            else:
                st.caption(
                    f"{snap['status'] or 'Scan paused'} · "
                    f"{snap['processed']}/{total} attempted"
                )
        with right:
            if snap["started"] and not our_job and not busy:
                if st.button("Reset results", key="fno_straddle_reset", use_container_width=True):
                    snap.update(rows={}, otm_calls={}, errors={}, processed=0,
                                status="", fatal="", started=False)
                    st.rerun()

        cid, token = _local_creds()
        if not valid_client_id(cid):
            cid = st.text_input(
                "Dhan Client ID", key="fno_straddle_client_id",
                help="Only used for read-only market data; saved locally after you start the scan.",
            ).strip()
        if not token and not (valid_client_id(cid) and is_configured(cid)):
            token = st.text_input(
                "Dhan market-data access token", type="password",
                key="fno_straddle_token",
                help="No token is saved to the stock list, CSV or GitHub.",
            ).strip()
        automatic = valid_client_id(cid) and is_configured(cid) and not token

        left_button, right_button = st.columns([1, 1])
        label = (
            f"Scan all {total} ATM straddles" if not snap["started"]
            else "Resume remaining / retry unavailable"
        )
        with left_button:
            start = st.button(
                label,
                key="fno_straddle_start",
                type="primary",
                disabled=busy or total == 0,
                use_container_width=True,
            )
        with right_button:
            if st.button(
                "Stop scan",
                key="fno_straddle_stop",
                disabled=not our_job, use_container_width=True,
            ) and our_job:
                active["cancel"].set()
                snap["status"] = "Stopping after current Dhan request…"

        if start:
            if not valid_client_id(cid):
                st.error("Enter your valid Dhan Client ID locally.")
            elif len(token) < 16 and not automatic:
                st.error("Enter a valid Dhan access token or enable local TOTP login.")
            else:
                if read_client_id() != cid:
                    save_client_id(cid)
                queue, cancel = SimpleQueue(), Event()
                snap["started"] = True
                snap["status"] = "Starting full-universe scan"
                snap["fatal"] = ""
                done = set(snap["rows"])
                snap["processed"] = len(done)
                st.session_state["fno_straddle_job"] = {
                    "key": key, "queue": queue, "cancel": cancel,
                    "future": _straddle_pool().submit(
                        scan_all_straddles, list(available), expiry,
                        cid, token, queue, cancel,
                        existing=done, auto_login=automatic,
                    ),
                }
                st.rerun()

        if busy and not our_job:
            st.info(
                "An ATM straddle scan for another expiry is already running. "
                "Stop that scan before starting a new one."
            )
        if snap["fatal"]:
            st.error(
                "Scan stopped: " + snap["fatal"]
                + ". Successful stock results remain available to resume."
            )
        elif snap["status"] and snap["status"] not in ("Complete", ""):
            st.caption(snap["status"])
        if snap["started"]:
            st.progress(
                min(1.0, snap["processed"] / max(1, total)),
                text=f"{snap['processed']}/{total} stocks attempted",
            )

    if snap["rows"]:
        df = straddle_dataframe(snap["rows"], snap.get("otm_calls", {}))
        st.caption(
            f"Dhan option chain · {len(df)} ranked / {total} · "
            "percentage based on option LTP (not guaranteed sale price)"
        )
        display_df = clickable_stock_frame(
            df, source="Stock", target="TradingView chart",
        )
        st.dataframe(
            display_df, use_container_width=True, hide_index=True, height=540,
            key=dataframe_key("ccs_straddle_rankings", st.session_state),
            column_config=autofit_column_config({
                "Rank": st.column_config.NumberColumn("#", format="%d"),
                "Stock": None,  # Keep raw NSE symbol for calculations/CSV.
                "TradingView chart": st.column_config.LinkColumn(
                    "Stock", display_text=TRADINGVIEW_SYMBOL_DISPLAY,
                    help="Click to open this NSE stock in TradingView.",
                    width="small",
                ),
                "ATM strike (₹)": st.column_config.NumberColumn("ATM (₹)", format="₹%.2f"),
                "CMP (₹)": st.column_config.NumberColumn("CMP (₹)", format="₹%.2f"),
                "CE LTP (₹)": st.column_config.NumberColumn("CE (₹)", format="₹%.2f"),
                "PE LTP (₹)": st.column_config.NumberColumn("PE (₹)", format="₹%.2f"),
                "CE+PE (₹)": st.column_config.NumberColumn("CE+PE (₹)", format="₹%.2f"),
                "Straddle premium (%)": st.column_config.NumberColumn(
                    "Straddle % ↓", format="%.2f%%",
                    help="(CE LTP + PE LTP) ÷ Dhan chain underlying LTP × 100",
                ),
                "Gross credit/lot (₹)": st.column_config.NumberColumn(
                    "Gross ₹/lot", format="₹%.0f",
                ),
                "Bid-based (%)": st.column_config.NumberColumn(
                    "Bid % (indicative)", format="%.2f%%",
                    help="CE bid + PE bid as % of underlying, where both bids exist",
                ),
                "5%+ CE premium (%)": st.column_config.NumberColumn(
                    "CE ≥5% OTM", format="%.2f%%",
                    help="First listed CE strike ≥ Dhan spot ×1.05. CE last price ÷ spot ×100.",
                ),
                "5%+ CE strike (₹)": st.column_config.NumberColumn(
                    "5%+ CE strike", format="₹%.2f",
                ),
                "Expiry": st.column_config.TextColumn("Expiry"),
            }, autofit_enabled(st.session_state)),
        )
        st.download_button(
            "Export ranked straddles (CSV)",
            data=df.to_csv(index=False),
            file_name=f"atm_straddle_ranking_{expiry}.csv",
            mime="text/csv",
            key="fno_straddle_export",
        )
        if snap["processed"] < total:
            st.caption(
                "Partial ranking — additional stocks may enter above existing rows "
                "until all stocks have been checked."
            )
    elif not snap["started"]:
        st.caption("No option chains loaded yet. The stock-price screener needs no Dhan login.")

    with st.expander("Unavailable stocks and quote details", expanded=False):
        st.caption(
            "Dhan option-chain snapshots may be delayed or stale, especially "
            "outside exchange hours. Exact expiry dates can differ by stock."
        )
        if snap["errors"]:
            st.dataframe(
                [{"Stock": sym, "Reason": msg} for sym, msg in sorted(snap["errors"].items())],
                hide_index=True, use_container_width=True,
                key=dataframe_key("ccs_straddle_unavailable", st.session_state),
            )
        if snap["rows"]:
            ordered = rank_straddles(snap["rows"])
            st.caption(f"Last stock snapshot: {ordered[-1].captured_at}")
        st.caption("No credentials, PIN or TOTP secret are included in CSV exports.")

    st.caption(
        "A short straddle has substantial downside and potentially unlimited "
        "upside loss. Premium % is gross credit relative to stock LTP, **not "
        "profit probability, net return or required margin**."
    )
