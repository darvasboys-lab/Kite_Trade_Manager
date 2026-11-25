"""Streamlit UI for full-universe, descending ATM short-straddle ranking.

After successful, persisted, one-time Dhan setup the Live CE mode may start
ONE paced read-only full-universe scan per session/day. Before verification
there are no automatic option-chain requests. User retains Stop/Resume.
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

from .auto_auth import is_configured
from .ce_eod_remote import IST
from .dhan_integration_state import verified_account
from .local_client_settings import read_client_id, save_client_id, valid_client_id
from .option_panel import _local_creds
from .straddle_scan import otm_call_5_from_atm, rank_straddles, scan_all_straddles
from .load_priority import strongest_first
from .quote_routing import nse_regular_session
from .table_layout import autofit_column_config, autofit_enabled, dataframe_key
from .tradingview_links import clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY


@st.cache_resource
def _straddle_pool():
    # Process-wide Dhan request limiter still serializes option-chain HTTP.
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="dhan-straddle-scan")


def available_otm_calls(available, expiry: str) -> dict:
    """Reuse verified +5% CE from full scan OR the one-stock Dhan test.

    No additional Dhan request. The one-stock result is scoped to the
    current Dhan client ID and selected expiry to avoid cross-account reuse.
    """
    history = st.session_state.get("fno_straddle_snapshots") or {}
    snap = history.get(_scan_key(available, expiry)) or {}
    cid, _ = _local_creds()
    results = dict(snap.get("otm_calls") or {})
    if snap.get("client_id") and snap["client_id"] != cid:
        results.clear()

    single = st.session_state.get("fno_atm_result") or {}
    if (cid and single.get("cid") == cid
            and single.get("month") == expiry
            and single.get("symbol") in {
                item.entry.symbol for item in available
            }):
        quote = single.get("quote")
        if quote is not None:
            fresh = otm_call_5_from_atm(quote)
            if fresh is not None:
                results[single["symbol"]] = fresh
    return results


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
def render_straddle_panel(available, expiry, *, priority_levels=None,
                          priority_quotes=None):
    """Live progress and sorting for the entire expiry universe, one user click."""
    total = len(available)
    key = _scan_key(available, expiry)
    history = st.session_state.setdefault("fno_straddle_snapshots", {})
    cid, token = _local_creds()
    snap = history.get(key)
    # Prevent old broker-account rows from blocking a new account's scan.
    if (snap and snap.get("client_id") and snap["client_id"] != cid
            and not st.session_state.get("fno_straddle_job")):
        snap = None
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
    if not st.session_state.get("fno_straddle_intro_seen"):
        st.toast(
            "Live CE and ATM straddle use one paced Dhan option chain per stock. "
            "Each response must have genuine positive LTPs.", icon="📈",
        )
        st.session_state["fno_straddle_intro_seen"] = True
    with st.container(border=True):
        left, right = st.columns([2.1, 1])
        with left:
            st.markdown(
                f"**All {total} stock F&O underlyings** · "
                f"{len(snap['rows'])} valid straddles"
            )
            if not snap["started"]:
                st.caption("Ready · Dhan option chain (paced, read-only)")
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

        # After validated integration, the first Live CE visit schedules
        # one full scan, prioritized by strongest signals. It is never
        # repeated on 2-second UI refreshes or after a user stops it.
        # Reopening the app may trigger one new daily session snapshot.
        auto_key = f"{cid}:{key}:{datetime.now(IST).date().isoformat()}"
        auto_start = (
            not start
            and not snap["started"]
            and not busy
            and total > 0
            and st.session_state.get("fno_ce5_mode") == "Live"
            and nse_regular_session()
            and valid_client_id(cid) and len(token) >= 16
            and verified_account(cid) is not None
            and st.session_state.get("fno_straddle_auto_started_key") != auto_key
        )
        if auto_start:
            st.session_state["fno_straddle_auto_started_key"] = auto_key
            st.caption("Live CE · starting a single paced Dhan scan…")
        if start or auto_start:
            if not valid_client_id(cid):
                st.error("Enter your valid Dhan Client ID locally.")
            elif len(token) < 16 and not automatic:
                st.error("Enter a valid Dhan access token or enable local TOTP login.")
            else:
                if read_client_id() != cid:
                    save_client_id(cid)
                queue, cancel = SimpleQueue(), Event()
                priority_symbols = strongest_first(
                    [item.entry.symbol for item in available],
                    priority_levels, priority_quotes,
                )
                snap["started"] = True
                snap["client_id"] = cid
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
                        priority_symbols=priority_symbols,
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
