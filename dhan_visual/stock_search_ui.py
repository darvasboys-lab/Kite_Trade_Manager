"""On-demand one-stock details, independent of market shortlist filters."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

from .ce_eod_remote import IST
from .fyers_readonly import (FyersDataError, fetch_strategy_quotes, fetch_chain,
                             resolve_expiry_timestamp, fetch_daily_levels,
                             fetch_r1_intraday_bars)
from .fyers_ui import _scope
from .mobile_cards import card_fields, card_value
from .r1_today import session_r1_cross
from .screener import expiry_options
from .tradingview_links import tradingview_chart_url

OPEN = "ccs_stock_search_open"
RESULT = "ccs_stock_search_result"


def positive(value):
    try:
        x = Decimal(str(value))
        return x if x.is_finite() and x > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def load_stock_details(access, entry, expiry):
    """At most five existing read-only calls for the chosen stock; stop on failure."""
    if expiry not in entry.lots:
        raise ValueError("Expiry not in the verified NSE catalogue.")
    symbol = entry.symbol
    today = datetime.now(IST).date()
    months = expiry_options([entry])
    # Up to three catalogue months, always including the selected month.
    months = [expiry] + [m for m in months if m != expiry][:2]
    out = {"symbol": symbol, "expiry": expiry, "quotes": {}, "call": None,
           "straddle": None, "levels": None, "cross": None, "error": "",
           "at": datetime.now(IST).strftime("%d %b %H:%M:%S IST")}
    try:
        out["quotes"] = fetch_strategy_quotes(access, symbol, months)
        epoch, out["expiry_date"] = resolve_expiry_timestamp(access, expiry, probe=symbol)
        out["call"], out["straddle"] = fetch_chain(access, symbol, expiry, epoch)
        out["levels"] = fetch_daily_levels(access, symbol, day=today)
        pivot = positive(getattr(out["levels"], "monthly_r1", None))
        if pivot is not None:
            bars = fetch_r1_intraday_bars(access, symbol, day=today)
            cross, last = session_r1_cross(bars, pivot, today)
            out["cross"] = ("Yes" if cross else "No") if last is not None else None
    except FyersDataError as exc:
        out["error"] = str(exc)
    except Exception:
        out["error"] = "Selected stock data request stopped."
    return out


def detail_fields(entry, data):
    """Keep spot/contract/futures basis and option-chain underlying distinct."""
    q = data["quotes"]
    spot = positive(getattr(q.get("SPOT"), "price", None))
    future = positive(getattr(q.get(data["expiry"]), "price", None))
    lot = entry.lots[data["expiry"]]
    call, straddle, levels = data["call"], data["straddle"], data["levels"]
    row = {"CMP (₹)": spot, "Lot size": lot,
           "Contract size (₹)": spot * lot if spot else None,
           "Futures Price (₹)": future,
           "Futures vs CMP (%)": (future - spot) / spot * 100 if spot and future else None,
           "CE +5% (%)": getattr(call, "premium_pct", None),
           "CE strike (₹)": getattr(call, "strike", None),
           "CE premium (₹)": getattr(call, "call_ltp", None),
           "Option underlying (₹)": getattr(call, "underlying_ltp", None),
           "ATM strike (₹)": getattr(straddle, "strike", None),
           "ATM CE (₹)": getattr(straddle, "call_ltp", None),
           "ATM PE (₹)": getattr(straddle, "put_ltp", None),
           "ATM straddle (₹)": straddle.call_ltp + straddle.put_ltp if straddle else None,
           "ATM straddle (%)": getattr(straddle, "premium_pct", None),
           "R1 crossed today": data["cross"]}
    for name, attr in (("EMA 200", "ema200"), ("EMA 50", "ema50"),
                       ("EMA 20", "ema20"), ("EMA 10", "ema10"), ("Monthly R1", "monthly_r1")):
        level = positive(getattr(levels, attr, None))
        row[name + " (₹)"] = level
        row["Above " + name] = ("Yes" if spot > level else "No") if spot and level else None
    return row


def render_stock_search(access, universe):
    from .stock_strategy_ui import launch_strategy
    st.markdown("### Search stock")
    if st.button("← Back to Home", key="ccs_stock_search_back", use_container_width=True):
        st.session_state.pop(OPEN, None)
        st.rerun()
    entries = {x.entry.symbol:x.entry for x in universe}
    if not entries:
        st.info("No verified NSE F&O stocks.")
        return
    symbol = st.selectbox("Search by symbol or company name", sorted(entries),
                          format_func=lambda s: s + " · " + entries[s].name,
                          key="ccs_stock_search_symbol")
    entry = entries[symbol]
    months = expiry_options([entry])
    if not months:
        st.info("No verified expiry for this stock.")
        return
    expiry = st.selectbox("Expiry", months, key="ccs_stock_search_expiry_" + symbol)
    key = (_scope(access,[symbol],expiry), datetime.now(IST).date().isoformat())
    if st.button("Load / refresh stock details", key="ccs_stock_search_load",
                 type="primary", use_container_width=True):
        with st.spinner("Loading only " + symbol + "…"):
            st.session_state[RESULT] = {"key":key, "data":load_stock_details(access, entry, expiry)}
    saved = st.session_state.get(RESULT) or {}
    if saved.get("key") != key:
        st.caption("Choose a stock and expiry, then load. No whole-market scan is needed.")
        return
    data = saved["data"]
    row = detail_fields(entry, data)
    st.markdown("#### " + symbol)
    st.caption(f"{entry.name} · {expiry} · requested {data['at']}")
    if data["error"]:
        st.warning(data["error"] + " · Partial details only. Refresh manually.")
    groups = [
        ("Price & contract", ["CMP (₹)","Lot size","Contract size (₹)","Futures Price (₹)","Futures vs CMP (%)"]),
        ("Covered call & ATM straddle", ["CE +5% (%)","CE strike (₹)","CE premium (₹)","Option underlying (₹)","ATM strike (₹)","ATM CE (₹)","ATM PE (₹)","ATM straddle (₹)","ATM straddle (%)"]),
        ("Technical levels", ["EMA 200 (₹)","Above EMA 200","EMA 50 (₹)","Above EMA 50","EMA 20 (₹)","Above EMA 20","EMA 10 (₹)","Above EMA 10","Monthly R1 (₹)","Above Monthly R1","R1 crossed today"])]
    for title, cols in groups:
        st.markdown("#### " + title)
        st.markdown(card_fields(row, [(c,c) for c in cols]), unsafe_allow_html=True)
    st.caption("CE and straddle percentages use the option-chain underlying; futures basis uses the displayed cash quote. Missing data is shown as a dash.")
    st.markdown("#### Futures by expiry")
    values = {m + " (₹)":getattr(q,"price",None) for m,q in data["quotes"].items() if m != "SPOT"}
    if values:
        st.markdown(card_fields(values, [(c,c) for c in values]), unsafe_allow_html=True)
    for label, preset in (("Covered call · "+card_value(row["CE +5% (%)"],"CE +5% (%)"),"Covered call"),
                          ("Short straddle · "+card_value(row["ATM straddle (%)"],"ATM straddle (%)"),"Short straddle"),
                          ("Open full strategy / option chain",None)):
        if st.button(label,key="ccs_search_strategy_"+str(preset),use_container_width=True,
                     disabled=(preset=="Covered call" and data["call"] is None)
                           or (preset=="Short straddle" and data["straddle"] is None)):
            launch_strategy(symbol,entry,expiry,preset)
    st.link_button("Open TradingView chart",tradingview_chart_url(symbol),use_container_width=True)
