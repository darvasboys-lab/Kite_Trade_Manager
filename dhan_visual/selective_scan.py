"""Explicit, staged FYERS scans. No market-data requests on page rendering."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import pandas as pd
import streamlit as st

from .ce_eod_remote import IST
from .fyers_ui import connected_fyers, _scope
from .fyers_readonly import (
    FyersDataError, fetch_cash_quotes, fetch_chain, resolve_expiry_timestamp,
    fetch_daily_levels, fetch_r1_intraday_bars,
)
from .otm_home import otm_home_dataframe, filter_home_contracts, _manual_futures_column
from .screener import choose_expiry, expiry_options
from .screener_ui import _auto_universe
from .technical_ui import attach_technical_columns
from .r1_today import session_r1_cross
from .tradingview_links import clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY
from .table_layout import dataframe_key, autofit_column_config, autofit_enabled, click_autofit

BATCH = 20
HISTORY_BATCH = 10
STATE = "ccs_selective_scan"


def _stamp():
    return datetime.now(IST).strftime("%d %b %H:%M:%S IST")


def new_snapshot(key, expiry, maximum, minimum):
    return dict(key=key, expiry=expiry, maximum=maximum, minimum=minimum,
                cash={}, calls={}, straddles={}, attempted=set(), levels={},
                crosses={}, r1_day="", cash_at="", options_at="", epoch=None,
                error="", stopped=False)


def contract_candidates(available, snapshot):
    frame = otm_home_dataframe(available, snapshot["cash"], {},
                               expiry=snapshot["expiry"])
    frame = filter_home_contracts(frame, snapshot["maximum"])
    return frame.sort_values("Contract size (₹)", na_position="last")


def qualified_rows(available, snapshot):
    frame = otm_home_dataframe(available, snapshot["cash"], snapshot["calls"],
                               straddles=snapshot["straddles"], expiry=snapshot["expiry"])
    frame = filter_home_contracts(frame, snapshot["maximum"])
    values = pd.to_numeric(frame["5%+ OTM CE (%)"], errors="coerce")
    return frame.loc[values.notna() & (values > snapshot["minimum"])].reset_index(drop=True)


def next_option_batch(access, available, snapshot, *, limit=BATCH):
    """Only contract-qualified, previously unattempted names; no retry loop."""
    candidates = contract_candidates(available, snapshot)["Stock"].tolist()
    pending = [s for s in candidates if s not in snapshot["attempted"]][:max(0, min(BATCH, limit))]
    if not pending or snapshot["stopped"]:
        return 0
    snapshot["error"] = ""
    done = 0
    try:
        if not snapshot["epoch"]:
            snapshot["epoch"], _ = resolve_expiry_timestamp(access, snapshot["expiry"], probe=pending[0])
        for symbol in pending:
            snapshot["attempted"].add(symbol)
            call, straddle = fetch_chain(access, symbol, snapshot["expiry"], snapshot["epoch"])
            if call is not None:
                snapshot["calls"][symbol] = call
            if straddle is not None:
                snapshot["straddles"][symbol] = straddle
            done += 1
    except FyersDataError as exc:
        snapshot["error"] = str(exc)
        snapshot["stopped"] = True
    except Exception:
        snapshot["error"] = "Option scan stopped after unexpected data error."
        snapshot["stopped"] = True
    snapshot["options_at"] = _stamp()
    return done


def _snapshot(access):
    snap = st.session_state.get(STATE)
    if not access or not snap or snap["key"] != _scope(access, [], snap["expiry"]):
        return None
    return snap


def _universe_rows(universe, expiry):
    return [item for item in universe if expiry in item.entry.lots]


def _display(frame, *, market=False):
    config = {
        "CMP (₹)": st.column_config.NumberColumn("CMP (₹)", format="₹%.2f"),
        "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
        "Contract size (₹)": st.column_config.NumberColumn("Contract size (₹)", format="₹%.0f"),
        "5%+ OTM CE (%)": st.column_config.NumberColumn("CE ≥ +5% (%)", format="%.2f%%"),
        "ATM Straddle Total (₹)": st.column_config.NumberColumn("ATM Straddle Total (₹)", format="₹%.2f"),
        "Straddle (%)": st.column_config.NumberColumn("Straddle (%)", format="%.2f%%"),
        "Futures Price (₹)": st.column_config.NumberColumn("Futures Price (₹)", format="₹%.2f"),
        "Futures vs CMP (%)": st.column_config.NumberColumn("Futures vs CMP (%)", format="%+.2f%%"),
    }
    if market:
        frame = clickable_stock_frame(frame, source="Stock", target="TradingView chart")
        config["Stock"] = None
        config["TradingView chart"] = st.column_config.LinkColumn("Stock", display_text=TRADINGVIEW_SYMBOL_DISPLAY)
    st.dataframe(frame, hide_index=True, use_container_width=True, height=520,
                 key=dataframe_key("selective_market" if market else "selective_home", st.session_state),
                 column_config=autofit_column_config(config, autofit_enabled(st.session_state)))


def render_selective_home():
    st.markdown("### OTM Covered Call · Data-saving scan")
    st.caption("Select filters first. Nothing is fetched from FYERS or Yahoo until you apply a scan.")
    access = connected_fyers()
    if not access:
        st.info("Connect and select FYERS in Settings to use the data-saving scan.")
        return
    universe = _auto_universe()
    if not universe:
        return
    entries = [item.entry for item in universe]
    expiries = expiry_options(entries)
    if not expiries:
        st.warning("No verified NSE expiry is available.")
        return
    snapshot = _snapshot(access)
    default_expiry = snapshot["expiry"] if snapshot and snapshot["expiry"] in expiries else choose_expiry(entries)
    with st.form("selective_scan_filters"):
        expiry = st.radio("Option month", expiries,
                          index=expiries.index(default_expiry), horizontal=True,
                          key="selective_expiry_choice")
        maximum = st.radio("Contract size below", list(range(4, 11)),
                           index=(snapshot["maximum"]-4 if snapshot else 3),
                           format_func=lambda value: f"₹{value}L", horizontal=True,
                           key="selective_contract_choice")
        minimum = st.radio("5%+ OTM CE premium above", [2, 3, 4, 5, 6, 7, 8],
                           index=(snapshot["minimum"]-2 if snapshot else 0),
                           format_func=lambda value: f"{value}%", horizontal=True,
                           key="selective_premium_choice")
        apply = st.form_submit_button("Apply filters / Start scan", type="primary",
                                      use_container_width=True)
    if apply:
        key = _scope(access, [], expiry)
        available = _universe_rows(universe, expiry)
        if not snapshot or snapshot["key"] != key:
            snapshot = new_snapshot(key, expiry, maximum, minimum)
            st.session_state[STATE] = snapshot
        snapshot.update(maximum=maximum, minimum=minimum)
        if not snapshot["cash_at"] and not snapshot["stopped"]:
            try:
                with st.spinner("Loading batched stock CMP…"):
                    snapshot["cash"] = fetch_cash_quotes(access, [item.entry.symbol for item in available])
                snapshot["cash_at"] = _stamp()
            except FyersDataError as exc:
                snapshot["error"] = str(exc)
                snapshot["stopped"] = True
            except Exception:
                snapshot["error"] = "Stock quote request stopped."
                snapshot["stopped"] = True
        if not snapshot["attempted"] and snapshot["cash_at"] and not snapshot["stopped"]:
            with st.spinner("Checking first eligible option contracts…"):
                next_option_batch(access, available, snapshot)
    if not snapshot:
        return
    available = _universe_rows(universe, snapshot["expiry"])
    candidates = contract_candidates(available, snapshot)
    pending = set(candidates["Stock"]) - snapshot["attempted"]
    a, b, c = st.columns(3)
    a.metric("Cash quotes", len(snapshot["cash"]))
    b.metric("Contract-qualified", len(candidates))
    c.metric("Option chains checked", len(set(candidates["Stock"]) & snapshot["attempted"]))
    st.caption(f"Applied: below ₹{snapshot['maximum']} lakh · CE > {snapshot['minimum']}% · {snapshot['expiry']} · CMP snapshot {snapshot['cash_at'] or 'not loaded'}")
    x, y, z = st.columns([2, 1.4, 1])
    if x.button(f"Scan next {min(BATCH, len(pending))} option chains",
                key="selective_next_options", disabled=not pending or snapshot["stopped"] or not snapshot["cash_at"]):
        with st.spinner("Checking eligible option contracts…"):
            next_option_batch(access, available, snapshot)
        st.rerun()
    if y.button("New scan / refresh all", key="selective_reset"):
        st.session_state.pop(STATE, None)
        st.rerun()
    if z.button("Auto-fit", key="selective_autofit"):
        click_autofit(st.session_state)
    if snapshot["error"]:
        st.warning(snapshot["error"] + " · Stopped. No automatic retries. Start a new scan manually when appropriate.")
    frame = qualified_rows(available, snapshot)
    st.markdown(f"**{len(frame)} qualifying stocks** · {len(pending)} eligible option chains not yet checked")
    if pending:
        st.caption("Partial scan: more matches may exist in the unscanned stocks. Continue only if needed.")
    if snapshot["options_at"]:
        st.caption("Options requested " + snapshot["options_at"] + " · cached snapshot; not automatically refreshed.")
    frame = _manual_futures_column(frame, snapshot["expiry"])
    _display(frame)


def render_selective_market():
    st.markdown("### Market Screener · Shortlisted stocks only")
    access = connected_fyers()
    snapshot = _snapshot(access)
    data = st.session_state.get("fno_universe_data")
    if not snapshot or not data:
        st.info("Run the filtered scan on Home first. This page has made no market-data requests.")
        return
    available = _universe_rows(data[0], snapshot["expiry"])
    frame = qualified_rows(available, snapshot)
    symbols = frame["Stock"].tolist()
    if not symbols:
        st.info("No qualifying stocks yet. Continue the Home scan if needed.")
        return
    st.caption("Uses Home's cached shortlist. Daily history and today's R1 crossing are separate manual requests.")
    selected = st.multiselect("Stocks for additional data (up to 10 per request)",
                              symbols, default=symbols[:HISTORY_BATCH], max_selections=HISTORY_BATCH,
                              key="selective_history_symbols")
    today = datetime.now(IST).date()
    if snapshot.get("levels_day") != today.isoformat():
        snapshot["levels"] = {}
        snapshot["levels_day"] = today.isoformat()
    if snapshot["r1_day"] != today.isoformat():
        snapshot["crosses"] = {}
        snapshot["r1_day"] = today.isoformat()
    a, b = st.columns(2)
    if a.button("Load EMA / monthly R1", key="selective_history", disabled=not selected):
        with st.spinner("Loading only selected daily histories…"):
            try:
                for symbol in selected:
                    if symbol not in snapshot["levels"]:
                        snapshot["levels"][symbol] = fetch_daily_levels(access, symbol, day=today)
            except FyersDataError as exc:
                st.warning(str(exc) + " · Stopped; no automatic retry.")
            except Exception:
                st.warning("Daily history scan stopped.")
    ready = [s for s in selected if s in snapshot["levels"] and snapshot["levels"][s].monthly_r1 is not None]
    if b.button("Check R1 crossed today", key="selective_r1", disabled=not ready):
        with st.spinner("Checking selected stocks' intraday candles…"):
            try:
                for symbol in ready:
                    bars = fetch_r1_intraday_bars(access, symbol, day=today)
                    cross, _ = session_r1_cross(bars, snapshot["levels"][symbol].monthly_r1, today)
                    snapshot["crosses"][symbol] = cross
            except FyersDataError as exc:
                st.warning(str(exc) + " · Stopped; no automatic retry.")
            except Exception:
                st.warning("Intraday scan stopped.")
    result = frame.rename(columns={"Stock": "Symbol", "CMP (₹)": "Last price (₹)"})
    result = attach_technical_columns(result, snapshot["levels"], stage=4)
    result = result.rename(columns={"Symbol": "Stock", "Last price (₹)": "CMP (₹)"})
    crossed = result[result["Stock"].map(lambda s: snapshot["crosses"].get(s) is not None)].copy()
    if not crossed.empty:
        crossed["R1 crossed at"] = crossed["Stock"].map(lambda s: snapshot["crosses"][s].first_bar_time)
        st.markdown("#### Crossed monthly R1 today")
        st.dataframe(crossed, hide_index=True, use_container_width=True)
    st.caption(f"{len(snapshot['levels'])} daily histories cached · {len(snapshot['crosses'])} intraday checks · no background loading")
    _display(result, market=True)
    st.download_button("Download shortlist CSV", result.to_csv(index=False),
                       file_name="covered_call_shortlist.csv", mime="text/csv")
