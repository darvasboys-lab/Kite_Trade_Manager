"""On-demand strategy workspace for one shortlisted NSE stock."""
from datetime import datetime
import math
from decimal import Decimal
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .ce_eod_remote import IST
from .fyers_ui import connected_fyers, _scope
from .fyers_readonly import (FyersDataError, fetch_strategy_expiries,
                             fetch_strategy_quotes, fetch_strategy_chain)
from .screener import expiry_sort_key
from .stock_strategy import Leg, expiry_legs, payoff, payoff_summary
from .tradingview_links import tradingview_chart_url

ACTIVE = "ccs_stock_strategy_active"
CACHE = "ccs_stock_strategy_cache"


def launch_strategy(symbol, entry, expiry, preset):
    # Pause automatic scanning while this separate on-demand data workspace is active.
    scan = st.session_state.get("ccs_selective_scan")
    if scan:
        scan["auto_scan"] = False
    st.session_state[ACTIVE] = dict(symbol=symbol, lots=dict(entry.lots),
                                    expiry=expiry, preset=preset)
    st.rerun()


def _money(value):
    return "Unlimited" if math.isinf(value) else f"₹{value:,.2f}"


def _chain_table(chain, spot):
    rows = []
    strikes = sorted({k[0] for k in chain})
    atm = min(strikes, key=lambda s: (abs(float(s)-spot), s)) if spot else None
    for strike in strikes:
        ce = chain.get((strike, "CE"), {})
        pe = chain.get((strike, "PE"), {})
        def value(leg, key):
            return float(leg[key]) if leg.get(key) is not None else None
        rows.append({"CE OI": value(ce,"oi"), "CE Bid": value(ce,"bid"),
                     "CE Ask": value(ce,"ask"), "CE LTP": value(ce,"ltp"),
                     "Strike": float(strike), "ATM": "●" if strike == atm else "",
                     "PE LTP": value(pe,"ltp"), "PE Bid": value(pe,"bid"),
                     "PE Ask": value(pe,"ask"), "PE OI": value(pe,"oi")})
    return pd.DataFrame(rows)


def _preset(name, chain, spot, expiry, lot):
    if spot is None or not chain:
        return None
    strikes = sorted({k[0] for k in chain})
    atm = min(strikes, key=lambda s:(abs(float(s)-spot),s))
    otm = next((s for s in strikes if float(s)>=spot*1.05
                and chain.get((s,"CE"),{}).get("ltp") is not None), None)
    def option(kind, strike, side):
        record = chain.get((strike,kind),{})
        if record.get("ltp") is None:
            return None
        return Leg(kind,side,expiry,float(strike),lot,float(record["ltp"]))
    if name in ("Short straddle","Long straddle"):
        side = "Sell" if name=="Short straddle" else "Buy"
        legs = [option("CE",atm,side),option("PE",atm,side)]
    elif name in ("Covered call","5% OTM CE"):
        legs = [option("CE",otm,"Sell")]
        if name=="Covered call":
            legs.insert(0,Leg("Equity","Buy","",0.,lot,spot))
    else:
        return []
    return legs if all(leg is not None for leg in legs) else None


def render_stock_strategy():
    ctx = st.session_state.get(ACTIVE)
    if not ctx:
        return
    head, back = st.columns([3,1])
    head.markdown(f"### {ctx['symbol']} · Build Strategy")
    if back.button("← Back to shortlist",key="ccs_strategy_back",use_container_width=True):
        st.session_state.pop(ACTIVE,None)
        st.rerun()
    st.caption("Read-only strategy modelling · hypothetical fills at displayed or edited prices · no orders. Home scan is paused; resume after returning.")
    access = connected_fyers()
    if not access:
        st.warning("Connect FYERS in Settings before loading market data.")
        return
    symbol = ctx["symbol"]
    day = datetime.now(IST).date().isoformat()
    months = sorted(ctx["lots"],key=expiry_sort_key)
    months = [m for m in months if expiry_sort_key(m) >= (int(day[:4]),int(day[5:7]))][:3]
    if not months:
        st.warning("No current monthly expiries in the verified NSE catalogue.")
        return
    scope = (_scope(access,[symbol],ctx["expiry"]),day)
    cache = st.session_state.setdefault(CACHE,{})
    if scope not in cache:
        # Bound memory; no market calls for any other stock.
        cache.clear()
        cache[scope] = dict(expiries=None,quotes=None,chains={},legs=[],errors={},preset_pending=ctx["preset"],revision=0)
    state = cache[scope]
    if st.button("Refresh selected stock data",key="ccs_strategy_refresh"):
        state.update(expiries=None,quotes=None,chains={},errors={})
    if not state["errors"].get("initial"):
        try:
            with st.spinner("Loading selected stock expiry dates and quotes…"):
                if state["expiries"] is None:
                    state["expiries"] = fetch_strategy_expiries(access,symbol,months)
                if state["quotes"] is None:
                    state["quotes"] = fetch_strategy_quotes(access,symbol,list(state["expiries"]))
        except FyersDataError as exc:
            state["errors"]["initial"] = str(exc)
        except Exception:
            state["errors"]["initial"] = "Could not load selected stock data."
    if state["errors"].get("initial"):
        st.warning(state["errors"]["initial"]+" · No automatic retries. Refresh manually when appropriate.")
        return
    expiries = list(state["expiries"])
    quotes = state["quotes"]
    spot = float(quotes["SPOT"].price) if "SPOT" in quotes else None
    cols = st.columns(1+len(expiries))
    cols[0].metric("Spot CMP",_money(spot) if spot is not None else "—")
    for col,month in zip(cols[1:],expiries):
        quote = quotes.get(month)
        col.metric(month+" Futures",_money(float(quote.price)) if quote else "—")
    if quotes:
        st.caption("FYERS snapshot requested "+next(iter(quotes.values())).timestamp+" · LTP is not a guaranteed fill")
    st.link_button("Open TradingView chart",tradingview_chart_url(symbol))
    expiry = st.radio("Option expiry",expiries,index=expiries.index(ctx["expiry"]) if ctx["expiry"] in expiries else 0,
                      format_func=lambda m:f"{m} · {state['expiries'][m][1]}",horizontal=True,key="ccs_strategy_expiry_"+symbol)
    lot = ctx["lots"][expiry]
    if expiry not in state["chains"] and not state["errors"]:
        try:
            with st.spinner("Loading selected expiry option chain…"):
                state["chains"][expiry] = fetch_strategy_chain(access,symbol,expiry,state["expiries"][expiry][0])
        except FyersDataError as exc:
            state["errors"][expiry] = str(exc)
        except Exception:
            state["errors"][expiry] = "Option chain unavailable."
    if state["errors"]:
        st.warning(" · ".join(state["errors"].values())+" · No automatic retries. Refresh manually.")
    chain = state["chains"].get(expiry,{})
    pending = state.pop("preset_pending",None)
    if pending and chain and not state["legs"]:
        preset = _preset(pending,chain,spot,expiry,lot)
        if preset is not None:
            state["legs"] = preset
            state["revision"] += 1
        else:
            st.info("Preset unavailable: required quoted legs are missing. Add legs manually.")
    chain_col, builder = st.columns([1.4,1])
    with chain_col:
        st.markdown("#### Option chain")
        st.caption(f"{expiry} · lot {lot} · up to 50 strikes each side, as returned by FYERS")
        if chain:
            st.dataframe(_chain_table(chain,spot),hide_index=True,use_container_width=True,height=450)
        else:
            st.info("No verified option chain for this expiry.")
        st.markdown("#### Add a leg")
        kinds = ["Equity","Futures"]+ (["CE","PE"] if chain else [])
        kind = st.radio("Instrument",kinds,horizontal=True,key="ccs_strategy_kind")
        side = st.radio("Side",["Buy","Sell"],horizontal=True,key="ccs_strategy_side")
        strike = Decimal(0)
        price = spot if kind=="Equity" else (float(quotes[expiry].price) if expiry in quotes else None)
        if kind in ("CE","PE"):
            strikes = sorted(s for (s,k),leg in chain.items() if k==kind and leg["ltp"] is not None)
            if strikes:
                strike = st.selectbox("Strike",strikes,key=f"ccs_strategy_strike_{symbol}_{expiry}_{kind}")
                price = float(chain[(strike,kind)]["ltp"])
            else:
                price = None
        qty = st.number_input("Shares" if kind=="Equity" else "Lots",min_value=1,max_value=100000 if kind=="Equity" else 100,value=lot if kind=="Equity" else 1,step=1,key=f"ccs_strategy_qty_{symbol}_{expiry}_{kind}")
        entry = st.number_input("Hypothetical entry price (₹)",min_value=0.,value=float(price or 0),step=.05,
                                key=f"ccs_strategy_price_{symbol}_{expiry}_{kind}_{strike}")
        if st.button("＋ Add leg",key="ccs_strategy_add",disabled=price is None or len(state["legs"])>=30,use_container_width=True):
            state["legs"].append(Leg(kind,side,"" if kind=="Equity" else expiry,float(strike),int(qty)*(1 if kind=="Equity" else lot),float(entry)))
            state["revision"] += 1
            st.rerun()
    with builder:
        st.markdown("#### Strategy legs")
        name = st.selectbox("Preset",["Covered call","Short straddle","Long straddle","5% OTM CE"],key="ccs_strategy_preset")
        a,b = st.columns(2)
        if a.button("Apply preset",key="ccs_strategy_apply",disabled=not chain):
            legs = _preset(name,chain,spot,expiry,lot)
            if legs is None:
                st.warning("Required option prices unavailable; existing legs retained.")
            else:
                state["legs"] = legs
                state["revision"] += 1
                st.rerun()
        if b.button("Clear legs",key="ccs_strategy_clear"):
            state["legs"] = []
            state["revision"] += 1
            st.rerun()
        edited = []
        for i,leg in enumerate(state["legs"]):
            with st.container(border=True):
                a,b = st.columns([3,1])
                a.markdown(f"**{leg.side} {leg.kind}** · {leg.strike:g}" if leg.kind in ("CE","PE") else f"**{leg.side} {leg.kind}**")
                if b.button("Remove",key=f"ccs_strategy_remove_{state['revision']}_{i}"):
                    state["legs"].pop(i)
                    state["revision"] += 1
                    st.rerun()
                leg_lot = 1 if leg.kind=="Equity" else ctx["lots"][leg.expiry]
                a,b = st.columns(2)
                units = a.number_input("Shares" if leg.kind=="Equity" else "Lots",min_value=1,max_value=1000000,
                                       value=leg.units//leg_lot,step=1,key=f"ccs_leg_qty_{state['revision']}_{i}")
                premium = b.number_input("Entry ₹",min_value=0.,value=leg.price,step=.05,key=f"ccs_leg_price_{state['revision']}_{i}")
                st.caption(leg.expiry or "Equity · applies to selected expiry scenario")
                edited.append(Leg(leg.kind,leg.side,leg.expiry,leg.strike,int(units)*leg_lot,float(premium)))
        state["legs"] = edited
    st.markdown("#### Expiry payoff")
    active = expiry_legs(state["legs"],expiry)
    excluded = len(state["legs"])-len(active)
    if excluded:
        st.warning(f"{excluded} leg(s) from other expiries excluded. This is not a combined calendar-spread valuation.")
    if not active:
        st.info("Add strategy legs to view the selected-expiry payoff.")
        return
    summary = payoff_summary(active)
    a,b,c = st.columns(3)
    a.metric("Maximum profit",_money(summary["max_profit"]))
    b.metric("Maximum loss",_money(summary["max_loss"]))
    c.metric("Breakeven(s)"," / ".join(f"₹{x:,.2f}" for x in summary["breakevens"]) or ("Zero payoff interval" if summary["flat_zero"] else "None"))
    center = spot or max((leg.strike or leg.price for leg in active),default=1)
    knots = [leg.strike for leg in active if leg.strike>0]+summary["breakevens"]
    low = max(0,min([center*.75]+knots)*.95)
    high = max([center*1.25]+knots)*1.05
    xs = sorted({low+(high-low)*i/240 for i in range(241)}|{k for k in knots if low<=k<=high})
    ys = [payoff(active,x) for x in xs]
    chart = go.Figure()
    for sign,color in [(1,"#48c9a1"),(-1,"#f08b91")]:
        chart.add_trace(go.Scatter(x=xs,y=[max(y,0) if sign==1 else min(y,0) for y in ys],
                                  fill="tozeroy",line=dict(color=color,width=2),mode="lines",
                                  name="Profit" if sign==1 else "Loss",hovertemplate="Spot ₹%{x:,.2f}<br>P&L ₹%{y:,.2f}<extra></extra>"))
    if spot is not None:
        chart.add_vline(x=spot,line_dash="dot",line_color="#80b8f3")
    chart.add_hline(y=0,line_color="#8d9cad")
    chart.update_layout(height=390,margin=dict(l=10,r=10,t=12,b=10),
                        template="plotly_dark" if st.session_state.get("ccs_dark_mode",True) else "plotly_white",
                        xaxis_title="Underlying price at expiry (₹)",yaxis_title="Hypothetical P&L (₹)",hovermode="x unified")
    st.plotly_chart(chart,use_container_width=True)
    st.caption("Expiry intrinsic payoff only. Excludes fees, taxes, margin, early exits and time value. Unlimited loss is not bounded by the chart range.")
