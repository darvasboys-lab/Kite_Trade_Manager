"""Sensibull-style covered-call payoff studio: offline-only Windows prototype.

No broker credentials, trading engine imports, server exposure, or order buttons.
All market data and fills are user-adjustable hypothetical DEMO inputs.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.graph_objects as go
import streamlit as st
from dhan_visual.strategy import Position, plan, exposure_after_fills, current_closeout
from dhan_visual.payoff import current_expiry_payoff, expiry_breakevens, theoretical_today_payoff

st.set_page_config(page_title="Covered Call | Payoff Studio", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.stApp {background:#0b111a; color:#e8f1fa}
.block-container {max-width:1470px; padding-top:1.2rem; padding-bottom:3rem}
header[data-testid="stHeader"] {background:transparent}
[data-testid="stMetric"] {background:#151f2c; border:1px solid #29394b; border-radius:15px; padding:16px 18px}
[data-testid="stMetricLabel"] {color:#8fa5bc}
[data-testid="stMetricValue"] {font-size:25px; color:#f0f7fe}
[data-testid="stTabs"] button {font-weight:650; font-size:14px}
[data-testid="stExpander"] {background:#111b27; border:1px solid #29394b; border-radius:13px}
.hdr {padding:26px 30px; margin-bottom:15px; background:linear-gradient(115deg,#142c32,#152133 75%,#101823);border:1px solid #294653;border-radius:19px}
.hdr .kicker {font-size:11px;color:#66d8c9;letter-spacing:.16em;font-weight:800}
.hdr h1 {margin:4px 0 7px; font-size:36px; font-weight:800;letter-spacing:-.035em}
.hdr p {color:#9eb2c6; margin:0; font-size:14px}
.pill {display:inline-block;border:1px solid #44605f;color:#9be3da;border-radius:7px;padding:4px 10px;font-size:11px;font-weight:750;margin:14px 7px 0 0}
.section-note {font-size:12px; color:#98a9bc; padding-bottom:9px}
</style>""",unsafe_allow_html=True)
st.markdown('''<div class="hdr"><span class="kicker">COVERED CALL · STRATEGY ANALYZER</span>
<h1>Payoff <span style="color:#6ee3d1">Studio</span></h1>
<p>Position risk, expiry payoff, stage-buy accumulation and 50% profit threshold — all in one workspace.</p>
<span class="pill">OFFLINE DEMO</span><span class="pill">NO LIVE DHAN DATA</span><span class="pill">NO BROKER ORDERS</span></div>''', unsafe_allow_html=True)

search, manage = st.tabs(["⌕ Search Stock", "▥ Manage Trade"])

with search:
    st.subheader("Search Stock")
    st.caption("Read-only Dhan stock and option lookup will be integrated here. No quote displayed in this version is live.")
    symbol = st.text_input("Search NSE F&O symbol",placeholder="Enter symbol (e.g., ABC)")
    a,b,c=st.columns(3)
    a.metric("Broker", "Not connected")
    b.metric("Spot / live BID", "Unavailable")
    c.metric("Expiry / OTM calls", "Unavailable")
    if symbol:
        st.info(f"{symbol.upper()}: awaiting verified Dhan instrument master, option quotes and position-reader integration.")
    st.warning("The Search Stock tab has no order placement. Your first equity buy and short call remain manual.")

with manage:
    st.info("DEMO ONLY: Example positions and quotes are simulated. No Dhan account is connected; no order can be placed.")
    with st.expander("Edit example position & scenario inputs",expanded=True):
        p1,p2,p3,p4=st.columns(4)
        avg=p1.number_input("First MANUAL batch average (₹)",min_value=0.05,value=1000.0,step=1.0)
        qty=p2.number_input("Manually owned shares",min_value=1,value=100,step=1)
        strike=p3.number_input("Short CALL strike (₹)",min_value=0.05,value=1200.0,step=1.0)
        contract=p4.number_input("Total short-call shares",min_value=1,value=500,step=1)
        q1,q2,q3,q4=st.columns(4)
        batches=q1.selectbox("Auto buying batches",options=list(range(1,21)),index=2)
        premium=q2.number_input("Actual call credit (DEMO ₹)",min_value=0.01,value=10000.0,step=100.0)
        tick=q3.number_input("Stock tick size (₹)",min_value=0.01,value=0.05,step=0.01,format="%.2f")
        full_costs=q4.number_input("Expected total strategy costs (₹)",min_value=0.0,value=0.0,step=25.0)

    try:
        position=Position(Decimal(str(avg)),int(qty),Decimal(str(strike)),int(contract),Decimal(str(premium)),Decimal(str(tick)))
        pv=plan(position,int(batches),full_costs)
    except ValueError as exc:
        st.error(f"No valid buying schedule: {exc}")
        st.stop()

    with st.expander("Hypothetical market prices & fill progress",expanded=True):
        a,b,c,d=st.columns(4)
        spot=a.number_input("Stock BID / indicative spot (₹)",min_value=0.05,value=1100.0,step=1.0)
        option_ask=b.number_input("Short call buyback ASK (₹ per share)",min_value=0.0,value=18.0,step=1.0)
        done=c.slider("Simulated completed auto batches",min_value=0,max_value=len(pv.stages),value=0)
        exit_cost=d.number_input("Costs to close NOW (₹)",min_value=0.0,value=0.0,step=25.0)

    shares, invested=exposure_after_fills(position,pv,done)
    closeout=current_closeout(position,pv,done,spot,option_ask,exit_cost)
    remaining=contract-shares
    future_cash=sum(stage.shares*stage.trigger for stage in pv.stages[done:])
    target=pv.profit_target
    be=expiry_breakevens(position,pv,done,full_costs)

    a,b,c,d=st.columns(4)
    a.metric("Current closeout P&L · DEMO",f"₹{closeout:,.0f}",help="Simulated stock BID and option buyback ASK after current shares' assumed fills and closeout costs")
    b.metric("Conditional capped payoff",f"₹{pv.projected_profit_at_strike:,.0f}",help="Only if all remaining shares fill at planned prices and call payoff is assessed at expiry")
    c.metric("50% booking target",f"₹{target:,.0f}")
    d.metric("Shares / contract",f"{shares:,} / {contract:,}")

    payoff_tab, stage_tab, details_tab = st.tabs(["▤ Payoff Graph", "▦ Buy Stages", "☷ Position Details"])
    with payoff_tab:
        left,right=st.columns([3,1],gap="large")
        with right:
            st.markdown("#### Chart controls")
            model_today=st.checkbox("Show theoretical today",value=True)
            show_current=st.checkbox("Show current-position expiry",value=True)
            show_target=st.checkbox("Show 50% threshold",value=True)
            iv=st.slider("Model IV (%)",5,120,28,1,disabled=not model_today)
            dte=st.slider("Days to expiry (model)",0,180,21,1,disabled=not model_today)
            st.caption("Theoretical today uses Black–Scholes with an illustrative volatility and 6% annual risk-free rate. It is NOT a broker option quote or a guaranteed current P&L curve.")
            if remaining:
                st.error(f"UNCOVERED: {remaining:,} short-call shares. Current expiry loss can grow as the stock rises.")
            else:
                st.success("Simulated share quantity matches short-call quantity; actual coverage still unverified.")
            if closeout >= target:
                st.warning("DEMO target exceeded. This is not a live notification or sell signal.")
        with left:
            st.markdown("### Strategy payoff")
            st.caption("X = hypothetical underlying price · Y = combined profit/loss. Solid = conditional fully covered at expiry; dashed = current shares at expiry.")
            lo=max(0.05,min(float(avg),float(spot),float(strike))*0.60)
            hi=max(float(strike),float(spot),float(avg))*1.50
            xs=[lo+(hi-lo)*i/240 for i in range(241)]
            ys=[float(current_expiry_payoff(position,pv,done,x,full_costs)) for x in xs]
            conditional=[float(current_expiry_payoff(position,pv,len(pv.stages),x,full_costs)) for x in xs]
            chart=go.Figure()
            chart.add_trace(go.Scatter(x=xs,y=[max(y,0) for y in conditional],mode="lines",line=dict(width=0),fill="tozeroy",fillcolor="rgba(37,181,129,.16)",showlegend=False,hoverinfo="skip"))
            chart.add_trace(go.Scatter(x=xs,y=[min(y,0) for y in conditional],mode="lines",line=dict(width=0),fill="tozeroy",fillcolor="rgba(238,92,105,.17)",showlegend=False,hoverinfo="skip"))
            chart.add_trace(go.Scatter(x=xs,y=conditional,mode="lines",line=dict(color="#42d7a5",width=3.6),name="Full coverage · expiry",hovertemplate="Stock ₹%{x:,.2f}<br>Planned P&L ₹%{y:,.0f}<extra></extra>"))
            if show_current:
                chart.add_trace(go.Scatter(x=xs,y=ys,mode="lines",line=dict(color="#9b9dff",width=2.8,dash="dash"),name="Current position · expiry",hovertemplate="Stock ₹%{x:,.2f}<br>Current position expiry P&L ₹%{y:,.0f}<extra></extra>"))
            if model_today:
                today=[float(theoretical_today_payoff(position,pv,done,max(x,0.01),iv,dte)) for x in xs]
                chart.add_trace(go.Scatter(x=xs,y=today,mode="lines",line=dict(color="#f7bd68",width=2.0,dash="dot"),name="Modelled today · hypothetical",hovertemplate="Stock ₹%{x:,.2f}<br>Modelled P&L ₹%{y:,.0f}<extra></extra>"))
            chart.add_trace(go.Scatter(x=[spot],y=[float(closeout)],mode="markers",marker=dict(color="#ffffff",size=12,line=dict(color="#2f3545",width=3)),name="Current input quote P&L",hovertemplate="Input stock BID ₹%{x:,.2f}<br>Input closeout P&L ₹%{y:,.0f}<extra></extra>"))
            chart.add_hline(y=0,line_color="#8291a5",line_width=1.2)
            chart.add_vline(x=float(strike),line_color="#ffca79",line_dash="dot",annotation_text="Short call strike",annotation_position="top right")
            chart.add_vline(x=float(spot),line_color="#90c8fa",line_dash="dot",annotation_text="Input stock price",annotation_position="top left")
            if show_target:
                chart.add_hline(y=float(target),line_color="#ffd184",line_dash="dash",annotation_text="50% target",annotation_position="top right")
            for i,val in enumerate(be.planned_expiry):
                if lo<=float(val)<=hi:
                    chart.add_vline(x=float(val),line_dash="dash",line_color="#35bb96",opacity=.55)
            chart.update_layout(template="plotly_dark",height=500,margin=dict(l=12,r=12,t=28,b=12),paper_bgcolor="#111a27",plot_bgcolor="#111a27",font=dict(color="#a9bacd"),legend=dict(orientation="h",yanchor="bottom",y=1.02,x=0,font=dict(size=11)),xaxis_title="Underlying stock price (₹)",yaxis_title="P&L (₹)",hovermode="x unified")
            chart.update_xaxes(showgrid=True,gridcolor="#223142",zeroline=False)
            chart.update_yaxes(showgrid=True,gridcolor="#223142",zeroline=False,tickformat=",")
            st.plotly_chart(chart,use_container_width=True)
            st.caption("The coloured curve assumes every pending equity order fills at its planned trigger before expiry. Unfilled stages are **not** owned. Options mark-to-market can differ from the theoretical model; current P&L above is based only on your editable sample BID/ASK.")

    with stage_tab:
        st.markdown("### Buying-stage map")
        st.caption("The first batch is manually bought. The selected number of **automatic** batches divide the remaining price difference up to 0.5% below strike.")
        a,b,c,d=st.columns(4)
        a.metric("Original average",f"₹{avg:,.2f}")
        b.metric("Final trigger",f"₹{pv.final_trigger:,.2f}")
        c.metric("Price interval",f"₹{pv.price_interval:,.2f}")
        d.metric("Remaining planned cash",f"₹{future_cash:,.0f}")
        staged=[{"Batch":"First · MANUAL","Trigger":"—","Avg / target (₹)":f"{avg:,.2f}","Shares":qty,"Estimated cost (₹)":f"{avg*qty:,.2f}","Status":"Manual first buy (demo)"}]
        for s in pv.stages:
            staged.append({"Batch":f"Automatic {s.index}","Trigger":f"₹{s.trigger:,.2f}","Avg / target (₹)":f"{s.trigger:,.2f}","Shares":s.shares,"Estimated cost (₹)":f"{s.trigger*s.shares:,.2f}","Status":"Filled (demo)" if s.index<=done else "Pending (demo)"})
        st.dataframe(staged,hide_index=True,use_container_width=True)
        st.progress(min(shares/contract,1.0),text=f"Simulated coverage {shares}/{contract} shares ({shares/contract:.0%})")
        st.warning("A price trigger is NOT a guarantee that the order fills. Production activation requires Dhan order/holding reconciliation, risk checks and duplicate-buy protection.")

    with details_tab:
        st.markdown("### Position economics")
        r1,r2,r3=st.columns(3)
        r1.metric("Initial manual equity cost",f"₹{pv.initial_cost:,.0f}")
        r2.metric("Simulated equity invested",f"₹{invested:,.0f}")
        r3.metric("Projected total equity cost",f"₹{pv.projected_all_in_cost:,.0f}")
        r4,r5,r6=st.columns(3)
        r4.metric("Call premium credit",f"₹{premium:,.0f}")
        r5.metric("Input short-call buyback",f"₹{option_ask*contract:,.0f}")
        r6.metric("Current expiry breakevens",", ".join(f"₹{v:,.2f}" for v in be.current_expiry) or "None in range")
        st.markdown("**Conditional fully covered expiry breakeven:** " + (", ".join(f"₹{v:,.2f}" for v in be.planned_expiry) or "No positive breakeven below strike"))
        st.warning("The partially uncovered short-call exposure can have substantial downside and upside risk. The conditional profit cap only applies if all required shares actually fill. Profit target is an advisory; no closing order is ever sent.")
        st.caption("Before any future Dhan connection, reconcile original first-batch fills, options transaction credit, expiry/lot size, holdings and today's tradebook, mark-data freshness, account funds and service isolation from ResultBot.")
