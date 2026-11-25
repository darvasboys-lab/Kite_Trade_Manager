"""Explicit, staged FYERS scans. No market-data requests on page rendering."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from decimal import Decimal, InvalidOperation
import time
import pandas as pd
import streamlit as st

from .ce_eod_remote import IST
from .fyers_ui import connected_fyers, _scope
from .fyers_readonly import (
    FyersDataError, fetch_cash_quotes, fetch_chain, resolve_expiry_timestamp,
    fetch_daily_levels, fetch_r1_intraday_bars, fetch_futures_quotes,
)
from .otm_home import otm_home_dataframe, filter_home_contracts, attach_futures_columns
from .screener import choose_expiry, expiry_options
from .screener_ui import _auto_universe
from .technical_ui import attach_technical_columns
from .r1_today import session_r1_cross
from .tradingview_links import clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY
from .table_layout import dataframe_key, autofit_column_config, autofit_enabled, click_autofit

BATCH = 35
BATCH_INTERVAL_SECONDS = 60.0
HISTORY_BATCH = 10
STATE = "ccs_selective_scan"
FUTURES_STATE = "ccs_futures_discount_snapshot"
FUTURES_MAX_LAKH = 7
R1_ALL = "Include all stocks"
R1_ABOVE = "Above R1 only"
R1_COLUMN = "> R1"


def _stamp():
    return datetime.now(IST).strftime("%d %b %H:%M:%S IST")


def new_snapshot(key, expiry, maximum, minimum, r1_filter=R1_ALL):
    return dict(key=key, expiry=expiry, maximum=maximum, minimum=minimum,
                cash={}, calls={}, straddles={}, attempted=set(), levels={},
                r1_filter=r1_filter, levels_day="", history_attempted=set(),
                crosses={}, r1_day="", cash_at="", options_at="", epoch=None,
                error="", stopped=False, auto_scan=False, next_batch_at=0.0,
                scan_day="")


def contract_candidates(available, snapshot):
    frame = otm_home_dataframe(available, snapshot["cash"], {},
                               expiry=snapshot["expiry"])
    frame = filter_home_contracts(frame, snapshot["maximum"])
    return frame.sort_values("Contract size (₹)", na_position="last")


def premium_rows(available, snapshot):
    frame = otm_home_dataframe(available, snapshot["cash"], snapshot["calls"],
                               straddles=snapshot["straddles"], expiry=snapshot["expiry"])
    frame = filter_home_contracts(frame, snapshot["maximum"])
    values = pd.to_numeric(frame["5%+ OTM CE (%)"], errors="coerce")
    return frame.loc[values.notna() & (values > snapshot["minimum"])].reset_index(drop=True)


def _ensure_history_day(snapshot, today):
    if snapshot.get("levels_day") != today.isoformat():
        snapshot["levels"] = {}
        snapshot["history_attempted"] = set()
        snapshot["levels_day"] = today.isoformat()


def r1_status(cmp, levels):
    """Strictly above the current monthly pivot; missing evidence is unknown."""
    try:
        price = Decimal(str(cmp))
        pivot = Decimal(str(getattr(levels, "monthly_r1", None)))
        if price.is_finite() and pivot.is_finite() and price > 0 and pivot > 0:
            return "✅ Yes" if price > pivot else "No"
    except (InvalidOperation, ValueError, TypeError):
        pass
    return "—"


def qualified_rows(available, snapshot):
    frame = premium_rows(available, snapshot)
    frame[R1_COLUMN] = [
        r1_status(cmp, snapshot["levels"].get(symbol))
        for symbol, cmp in zip(frame["Stock"], frame["CMP (₹)"])
    ]
    if snapshot.get("r1_filter", R1_ALL) == R1_ABOVE:
        frame = frame.loc[frame[R1_COLUMN] == "✅ Yes"]
    return frame.reset_index(drop=True)


def pending_r1_symbols(available, snapshot):
    attempted = snapshot.get("history_attempted", set())
    return [s for s in premium_rows(available, snapshot)["Stock"]
            if s not in snapshot["levels"] and s not in attempted]


def load_shortlist_history(access, snapshot, symbols, *, today=None):
    """One daily history per shortlisted name; shared with Market Screener."""
    today = today or datetime.now(IST).date()
    _ensure_history_day(snapshot, today)
    if snapshot["stopped"]:
        return 0
    attempted = snapshot.setdefault("history_attempted", set())
    count = 0
    try:
        for symbol in list(dict.fromkeys(symbols))[:HISTORY_BATCH]:
            if symbol in snapshot["levels"] or symbol in attempted:
                continue
            attempted.add(symbol)
            snapshot["levels"][symbol] = fetch_daily_levels(access, symbol, day=today)
            count += 1
    except FyersDataError as exc:
        snapshot["error"] = str(exc)
        snapshot["stopped"] = True
    except Exception:
        snapshot["error"] = "Daily history scan stopped."
        snapshot["stopped"] = True
    return count


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


def pending_option_symbols(available, snapshot):
    return [s for s in contract_candidates(available, snapshot)["Stock"]
            if s not in snapshot["attempted"]]


def automatic_work_pending(available, snapshot):
    return bool(pending_option_symbols(available, snapshot)
                or pending_r1_symbols(available, snapshot))


def run_automatic_batch(access, available, snapshot, *, now=None):
    """At most one batch per minute; all requests share existing FYERS pacing.

    Synchronous execution on the Streamlit session thread prevents overlapping
    automatic batches. A late tick never catches up by issuing extra batches.
    """
    now = time.monotonic() if now is None else now
    if (not snapshot.get("auto_scan") or snapshot["stopped"]
            or not snapshot["cash_at"]):
        return False
    if snapshot.get("scan_day") != datetime.now(IST).date().isoformat():
        snapshot["auto_scan"] = False
        return False
    if not automatic_work_pending(available, snapshot):
        snapshot["auto_scan"] = False
        return False
    if now < snapshot.get("next_batch_at", 0.0):
        return False
    # Reserve the next time slot BEFORE doing I/O, including on error.
    snapshot["next_batch_at"] = now + BATCH_INTERVAL_SECONDS
    next_option_batch(access, available, snapshot)
    if not snapshot["stopped"]:
        load_shortlist_history(access, snapshot, pending_r1_symbols(available, snapshot))
    if snapshot["stopped"] or not automatic_work_pending(available, snapshot):
        snapshot["auto_scan"] = False
    return True


@st.fragment(run_every="3s")
def _automatic_scan_heartbeat():
    """Only mounted on Home; never continue after navigation or logout."""
    if (st.session_state.get(FUTURES_STATE)
            or st.session_state.get("ccs_stock_strategy_active")
            or st.session_state.get("ccs_legacy_full_scan")
            or st.session_state.get("ccs_selective_page") != "◎ OTM CE Home"):
        return
    access = connected_fyers()
    snapshot = _snapshot(access)
    data = st.session_state.get("fno_universe_data")
    if not snapshot or not data or not snapshot.get("auto_scan"):
        return
    available = _universe_rows(data[0], snapshot["expiry"])
    if run_automatic_batch(access, available, snapshot):
        st.rerun(scope="app")
    elif not snapshot.get("auto_scan"):
        st.rerun(scope="app")
    else:
        seconds = max(0, int(snapshot.get("next_batch_at", 0) - time.monotonic()))
        st.caption(f"Automatic scan active · next batch in ~{seconds}s · "
                   f"up to {BATCH} new option chains per minute.")


def _snapshot(access):
    snap = st.session_state.get(STATE)
    if not access or not snap or snap["key"] != _scope(access, [], snap["expiry"]):
        return None
    return snap


def _universe_rows(universe, expiry):
    return [item for item in universe if expiry in item.entry.lots]


STRATEGY_COLUMNS = {
    "5%+ OTM CE (%)": "Covered call",
    "ATM Straddle Total (₹)": "Short straddle",
    "Straddle (%)": "Short straddle",
}


def strategy_cell_target(event, frame):
    """Resolve only a valid quoted action cell to its original dataframe row."""
    if not isinstance(event, dict):
        return None
    selection = event.get("selection")
    cells = selection.get("cells") if isinstance(selection, dict) else None
    if not isinstance(cells, (list, tuple)) or len(cells) != 1:
        return None
    cell = cells[0]
    if not isinstance(cell, (list, tuple)) or len(cell) != 2:
        return None
    index, column = cell
    if (type(index) is not int or not 0 <= index < len(frame)
            or not isinstance(column, str) or column not in STRATEGY_COLUMNS
            or column not in frame.columns):
        return None
    row = frame.iloc[index]
    try:
        price = Decimal(str(row[column]))
        if not price.is_finite() or price <= 0:
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return row["Stock"], STRATEGY_COLUMNS[column]


def _strategy_cell_callback(key, frame, entries, expiry, snapshot_key, scan_day):
    """Callback uses the exact displayed snapshot, never a newly sorted frame."""
    if (st.session_state.get("ccs_selective_page") != "◎ OTM CE Home"
            or st.session_state.get("ccs_legacy_full_scan")
            or st.session_state.get(FUTURES_STATE)
            or st.session_state.get("ccs_stock_strategy_active")):
        return
    access = connected_fyers()
    snapshot = _snapshot(access)
    if (not snapshot or snapshot["key"] != snapshot_key
            or snapshot["expiry"] != expiry or snapshot.get("scan_day") != scan_day
            or scan_day != datetime.now(IST).date().isoformat()):
        return
    target = strategy_cell_target(st.session_state.get(key), frame)
    if target and target[0] in entries:
        from .stock_strategy_ui import activate_strategy
        symbol, preset = target
        activate_strategy(symbol, entries[symbol], expiry, preset)


def _display(frame, *, market=False, futures=False, strategy_context=None):
    config = {
        "CMP (₹)": st.column_config.NumberColumn("CMP (₹)", format="₹%.2f"),
        R1_COLUMN: st.column_config.TextColumn("> R1", help="CMP strictly above current monthly R1. Dash = not verified."),
        "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
        "Contract size (₹)": st.column_config.NumberColumn("Contract size (₹)", format="₹%.0f"),
        "5%+ OTM CE (%)": st.column_config.NumberColumn("CE ≥ +5% (%)", format="%.2f%%", help="Click the quoted CE percentage to build a covered call."),
        "ATM Straddle Total (₹)": st.column_config.NumberColumn("ATM Straddle Total (₹)", format="₹%.2f", help="Click to build a short ATM straddle."),
        "Straddle (%)": st.column_config.NumberColumn("Straddle (%)", format="%.2f%%", help="Click to build a short ATM straddle."),
        "Futures Price (₹)": st.column_config.NumberColumn("Futures Price (₹)", format="₹%.2f"),
        "Futures vs CMP (%)": st.column_config.NumberColumn("Futures vs CMP (%)", format="%+.2f%%"),
    }
    raw_frame = frame.copy(deep=True) if strategy_context is not None else None
    # Display-only link: retain raw NSE symbols in snapshots and CSV exports.
    frame = clickable_stock_frame(frame, source="Stock", target="TradingView chart")
    config["Stock"] = None
    config["TradingView chart"] = st.column_config.LinkColumn(
        "Stock", display_text=TRADINGVIEW_SYMBOL_DISPLAY,
        help="Click the stock symbol to open its NSE cash chart in TradingView.",
    )
    key = dataframe_key("selective_futures" if futures else "selective_market" if market else "selective_home", st.session_state)
    selection = {}
    if strategy_context is not None and not raw_frame.empty:
        from functools import partial
        entries, expiry, snapshot_key, scan_day = strategy_context
        digest = sha256((snapshot_key + raw_frame.to_json(orient="split")).encode()).hexdigest()[:20]
        serial = st.session_state.get("ccs_strategy_launch_serial", 0)
        key += f"_cells_{digest}_{serial}"
        selection = dict(selection_mode="single-cell",
                         on_select=partial(_strategy_cell_callback, key, raw_frame,
                                           entries, expiry, snapshot_key, scan_day))
    st.dataframe(frame, hide_index=True, use_container_width=True, height=520,
                 key=key, column_config=autofit_column_config(config, autofit_enabled(st.session_state)),
                 **selection)


def futures_discount_frame(available, cash, futures, expiry):
    """Every priced contract strictly below 7 lakh, independent of CE/R1."""
    frame = otm_home_dataframe(available, cash, {}, expiry=expiry)
    frame = filter_home_contracts(frame, FUTURES_MAX_LAKH)
    frame = frame[["Stock", "CMP (₹)", "Lot size", "Contract size (₹)"]]
    frame = attach_futures_columns(frame, futures)
    return frame.sort_values(["Futures vs CMP (%)", "Stock"], ascending=[True, True],
                             na_position="last").reset_index(drop=True)


def load_futures_discount(access, universe, expiry):
    """One explicit full-universe cash snapshot, then batched qualified futures."""
    available = _universe_rows(universe, expiry)
    cash = fetch_cash_quotes(access, [item.entry.symbol for item in available])
    candidate = futures_discount_frame(available, cash, {}, expiry)
    symbols = candidate["Stock"].tolist()
    quotes = fetch_futures_quotes(access, symbols, expiry) if symbols else {}
    return futures_discount_frame(available, cash, quotes, expiry), len(available), len(cash)


def render_futures_discount(access, universe, expiry):
    key = (_scope(access, [], expiry), datetime.now(IST).date().isoformat())
    state = st.session_state.get(FUTURES_STATE)
    if not state or state["key"] != key:
        state = dict(key=key, expiry=expiry, frame=None, error="", at="")
        st.session_state[FUTURES_STATE] = state
    st.markdown("### Futures · Discount to Premium")
    st.caption(f"{expiry} · CMP × selected-expiry lot < ₹7,00,000 · independent of CE premium and R1 filters")
    a,b = st.columns(2)
    if a.button("Refresh all futures", key="ccs_futures_refresh", use_container_width=True):
        render_futures_discount_request(access, universe, expiry)
        st.rerun()
    if b.button("Hide futures / return to shortlist", key="ccs_futures_hide", use_container_width=True):
        st.session_state.pop(FUTURES_STATE, None)
        st.rerun()
    if state["error"]:
        st.warning(state["error"] + " · No automatic retry. Refresh manually.")
    frame = state["frame"]
    if frame is None:
        if not state["error"]:
            st.info("Snapshot expired or broker changed. Refresh manually to load the selected month.")
        return
    basis = pd.to_numeric(frame["Futures vs CMP (%)"], errors="coerce")
    priced = basis.notna()
    x,y,z = st.columns(3)
    x.metric("Contracts below ₹7 lakh",len(frame))
    y.metric("Trading at discount",int((basis<0).sum()))
    z.metric("Futures prices available",int(priced.sum()))
    st.caption(f"Snapshot requested {state['at']} · cash quotes {state['cash']}/{state['total']} eligible stocks. Missing futures prices appear last; missing CMP cannot establish contract size.")
    st.caption("Basis = (futures − CMP) / CMP × 100. Cash and futures are sequential snapshots, not simultaneous fills.")
    _display(frame, futures=True)
    st.download_button("Download futures CSV",frame.to_csv(index=False),file_name="ccs-futures-discount.csv",key="ccs_futures_csv")


def render_futures_discount_request(access, universe, expiry):
    # Request is only made after an explicit form submit or refresh click.
    key = (_scope(access, [], expiry), datetime.now(IST).date().isoformat())
    state = dict(key=key, expiry=expiry, frame=None, error="", at="")
    try:
        with st.spinner("Loading all sub-₹7 lakh stock futures in quote batches…"):
            state["frame"], state["total"], state["cash"] = load_futures_discount(access, universe, expiry)
        state["at"] = _stamp()
    except FyersDataError as exc:
        state["error"] = str(exc)
    except Exception:
        state["error"] = "Futures quote request stopped."
    st.session_state[FUTURES_STATE] = state


def render_selective_home():
    from .workspace_ui import prepare_filters, save_scan_controls, remember_pref, render_historical_shortlist
    from .stock_strategy_ui import ACTIVE, launch_strategy, render_stock_strategy
    if st.session_state.get(ACTIVE):
        render_stock_strategy()
        return
    st.markdown("### OTM Covered Call")
    st.caption("Select filters first. Nothing is fetched from FYERS or Yahoo until you apply a scan.")
    render_historical_shortlist()
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
    prepare_filters(expiries)
    with st.form("selective_scan_filters"):
        st.markdown("#### Quick Scan")
        left, right = st.columns(2)
        expiry = left.radio("Option month", expiries,
                          index=expiries.index(default_expiry), horizontal=True,
                          key="selective_expiry_choice")
        maximum = right.radio("Contract size below", list(range(4, 11)),
                           index=(snapshot["maximum"]-4 if snapshot else 3),
                           format_func=lambda value: f"₹{value}L", horizontal=True,
                           key="selective_contract_choice")
        minimum = left.radio("5%+ OTM CE premium above", [2, 3, 4, 5, 6, 7, 8],
                           index=(snapshot["minimum"]-2 if snapshot else 0),
                           format_func=lambda value: f"{value}%", horizontal=True,
                           key="selective_premium_choice")
        r1_filter = right.radio("Monthly R1 filter", [R1_ALL, R1_ABOVE],
                             index=int(bool(snapshot and snapshot.get("r1_filter") == R1_ABOVE)),
                             horizontal=True, key="selective_r1_choice",
                             help="Above R1 only excludes both below/equal R1 and unknown R1. "
                                  "History is requested only after contract and CE filters.")
        note, action, future_action = st.columns([2, 1, 1])
        note.caption("Read-only · no market-data requests before Start scan.")
        apply = action.form_submit_button("Apply filters / Start scan", type="primary",
                                         use_container_width=True)
        show_futures = future_action.form_submit_button("Show futures · < ₹7L", use_container_width=True)
    if show_futures:
        if snapshot:
            snapshot["auto_scan"] = False
        render_futures_discount_request(access, universe, expiry)
    if apply:
        st.session_state.pop(FUTURES_STATE, None)
    elif st.session_state.get(FUTURES_STATE):
        render_futures_discount(access, universe, expiry)
        return
    if apply:
        key = _scope(access, [], expiry)
        available = _universe_rows(universe, expiry)
        if (not snapshot or snapshot["key"] != key
                or snapshot.get("scan_day") != datetime.now(IST).date().isoformat()):
            snapshot = new_snapshot(key, expiry, maximum, minimum)
            st.session_state[STATE] = snapshot
        snapshot.update(maximum=maximum, minimum=minimum, r1_filter=r1_filter)
        _ensure_history_day(snapshot, datetime.now(IST).date())
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
        if snapshot["cash_at"] and not snapshot["stopped"]:
            snapshot["scan_day"] = datetime.now(IST).date().isoformat()
            snapshot["auto_scan"] = automatic_work_pending(available, snapshot)
            with st.spinner("Checking eligible option chains and shortlisted R1…"):
                run_automatic_batch(access, available, snapshot)
    if not snapshot:
        return
    _ensure_history_day(snapshot, datetime.now(IST).date())
    available = _universe_rows(universe, snapshot["expiry"])
    candidates = contract_candidates(available, snapshot)
    pending = set(candidates["Stock"]) - snapshot["attempted"]
    checked = len(set(candidates["Stock"]) & snapshot["attempted"])
    frame = qualified_rows(available, snapshot)
    a, b, c, d = st.columns(4)
    a.metric("Cash quotes", len(snapshot["cash"]))
    b.metric("Contract-qualified", len(candidates))
    c.metric("Option chains checked", f"{checked} / {len(candidates)}")
    d.metric("Qualifying opportunities", len(frame))
    st.caption(f"Applied: below ₹{snapshot['maximum']} lakh · CE > {snapshot['minimum']}% · {snapshot['expiry']} · {snapshot.get('r1_filter', R1_ALL)} · CMP snapshot {snapshot['cash_at'] or 'not loaded'}")
    st.progress(checked / len(candidates) if len(candidates) else 0.0,
                text=f"{checked} of {len(candidates)} option chains checked")
    x, y = st.columns(2)
    work_pending = automatic_work_pending(available, snapshot)
    today = datetime.now(IST).date().isoformat()
    if snapshot.get("scan_day") != today:
        snapshot["auto_scan"] = False
    automatic = bool(snapshot.get("auto_scan"))
    if x.button("Pause automatic scan" if automatic else "Resume automatic scan",
                key="selective_auto_scan",
                disabled=not work_pending or snapshot["stopped"] or not snapshot["cash_at"]
                         or snapshot.get("scan_day") != today):
        snapshot["auto_scan"] = not automatic
        st.rerun()
    if y.button("New scan / refresh all", key="selective_reset"):
        st.session_state.pop(STATE, None)
        st.rerun()
    if snapshot.get("auto_scan") and work_pending:
        _automatic_scan_heartbeat()
    elif work_pending and not snapshot["stopped"]:
        st.caption("Automatic scan paused. Resume to check remaining stocks.")
    elif not snapshot["stopped"]:
        st.success("All eligible option chains and shortlisted R1 histories checked.")
    if snapshot["error"]:
        st.warning(snapshot["error"] + " · Stopped. No automatic retries. Start a new scan manually when appropriate.")
    frame = qualified_rows(available, snapshot)
    title, fit = st.columns([3, 1])
    title.markdown("#### Qualified Opportunities")
    title.caption(f"{len(frame)} qualifying stocks · {len(pending)} eligible option chains not yet checked")
    if fit.button("Auto-fit", key="selective_autofit", use_container_width=True):
        click_autofit(st.session_state)
        remember_pref("autofit",True)
    unknown_r1 = sum(r1_status(cmp, snapshot["levels"].get(symbol)) == "—"
                     for symbol, cmp in zip(premium_rows(available, snapshot)["Stock"],
                                            premium_rows(available, snapshot)["CMP (₹)"]))
    if unknown_r1:
        st.caption(f"{unknown_r1} contract/CE matches have unverified R1; "
                   "above-only mode excludes these until verified.")
    if pending:
        st.caption("Partial scan: more matches may exist in the unscanned stocks. Automatic batches run only on Home while active.")
    if snapshot["options_at"]:
        st.caption("Options requested " + snapshot["options_at"] + " · each stock checked once; cached quotes are not refreshed automatically.")
    save_scan_controls(snapshot,frame)
    st.caption("Click any CE premium %, ATM straddle total or straddle % cell to open that stock’s Strategy Builder.")
    _display(frame, strategy_context=({item.entry.symbol: item.entry for item in available},
                                     snapshot["expiry"], snapshot["key"], snapshot.get("scan_day")))
    if not frame.empty:
        st.markdown("#### Build strategy")
        a, b, c = st.columns([1.5,1,1])
        chosen = a.selectbox("Stock", frame["Stock"].tolist(), key="ccs_strategy_stock")
        selected_row = frame.loc[frame["Stock"] == chosen].iloc[0]
        entry = next(item.entry for item in available if item.entry.symbol == chosen)
        ce = selected_row["5%+ OTM CE (%)"]
        straddle = selected_row["ATM Straddle Total (₹)"]
        if b.button(f"CE {ce:.2f}% · Build", key="ccs_strategy_from_ce", use_container_width=True):
            launch_strategy(chosen, entry, snapshot["expiry"], "Covered call")
        if c.button(f"Straddle ₹{straddle:.2f} · Build" if pd.notna(straddle) else "Straddle · Build",
                    key="ccs_strategy_from_straddle", disabled=pd.isna(straddle), use_container_width=True):
            launch_strategy(chosen, entry, snapshot["expiry"], "Short straddle")


def render_selective_market():
    st.markdown("### Market Screener · Shortlisted stocks only")
    access = connected_fyers()
    snapshot = _snapshot(access)
    data = st.session_state.get("fno_universe_data")
    if not snapshot or not data:
        st.info("Run the filtered scan on Home first. This page has made no market-data requests.")
        return
    today = datetime.now(IST).date()
    _ensure_history_day(snapshot, today)
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
    if snapshot["r1_day"] != today.isoformat():
        snapshot["crosses"] = {}
        snapshot["r1_day"] = today.isoformat()
    a, b = st.columns(2)
    if a.button("Load EMA / monthly R1", key="selective_history",
                disabled=not selected or snapshot["stopped"]):
        with st.spinner("Loading only selected daily histories…"):
            load_shortlist_history(access, snapshot, selected, today=today)
    if snapshot["error"]:
        st.warning(snapshot["error"] + " · Stopped; no automatic retry.")
    ready = [s for s in selected if s in snapshot["levels"] and snapshot["levels"][s].monthly_r1 is not None]
    if b.button("Check R1 crossed today", key="selective_r1", disabled=not ready or snapshot["stopped"]):
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
    frame = qualified_rows(available, snapshot)
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
