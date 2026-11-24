"""Sensibull-style covered-call payoff studio: offline-only Windows prototype.

The Search Stock screen uses a read-only local Dhan market-data connection; no trading functions or order buttons.
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
<span class="pill">OFFLINE DEMO</span><span class="pill">DHAN SCREEN · READ-ONLY</span><span class="pill">NO BROKER ORDERS</span></div>''', unsafe_allow_html=True)

search, manage = st.tabs(["⌕ Search Stock", "▥ Manage Trade"])

with search:
    # F&O public list loads automatically in a nonblocking worker; no trading calls.
    from dhan_visual.screener_ui import render_screener
    render_screener()

with manage:
    st.info("DEMO ONLY: Example positions and quotes are simulated. No Dhan account is connected; no order can be placed.")
    with st.expander("Edit strategy inputs",expanded=False):
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

    with st.expander("Edit demo market prices and fill progress",expanded=False):
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
        st.markdown("### Profit / loss at expiry")
        c1, c2 = st.columns([2, 1])
        with c1:
            selected_view = st.radio(
                "Show payoff for",
                ["Shares owned now", "After all planned buys"],
                horizontal=True, key="simple_payoff_view",
                help="One clear payoff line at a time. The planned view is conditional.",
            )
        with c2:
            st.caption(
                "Purple = current shares and short call · "
                "Green = fully covered after hypothetical future fills."
            )

        if remaining:
            st.warning(
                f"{remaining:,} short-call shares remain uncovered in this example. "
                "The **Shares owned now** view shows that risk."
            )
        else:
            st.success("Simulated current shares cover the short call.")
        if closeout >= target:
            st.caption("DEMO: the example closeout P&L exceeds the planned 50% target. No alert or order was sent.")

        with st.expander("Optional chart details", expanded=False):
            show_both = st.checkbox("Compare both expiry curves", value=False, key="graph_compare")
            show_target = st.checkbox("Show planned 50% target guide", value=False, key="graph_target")
            show_today = st.checkbox("Show theoretical today (model, not live quote)", value=False, key="graph_today")
            if show_today:
                a, b = st.columns(2)
                iv = a.slider("Model volatility (%)", 5, 120, 28, 1, key="graph_iv")
                dte = b.slider("Model days to expiry", 0, 180, 21, 1, key="graph_dte")
                st.caption("Black–Scholes is a scenario model only; it is not the broker's buyback ASK.")

        lo=max(0.05,min(float(avg),float(spot),float(strike))*0.65)
        hi=max(float(strike),float(spot),float(avg))*1.35
        xs=[lo+(hi-lo)*i/220 for i in range(221)]
        actual=[float(current_expiry_payoff(position,pv,done,x,full_costs)) for x in xs]
        planned=[float(current_expiry_payoff(position,pv,len(pv.stages),x,full_costs)) for x in xs]
        planned_view=selected_view=="After all planned buys"
        chosen=planned if planned_view else actual
        accent="#41d6ab" if planned_view else "#a8a1fd"

        chart=go.Figure()
        chart.add_trace(go.Scatter(
            x=xs,y=[max(y,0) for y in chosen],mode="lines",
            line=dict(width=0),fill="tozeroy",
            fillcolor="rgba(47,195,145,.16)",showlegend=False,hoverinfo="skip"))
        chart.add_trace(go.Scatter(
            x=xs,y=[min(y,0) for y in chosen],mode="lines",
            line=dict(width=0),fill="tozeroy",
            fillcolor="rgba(232,94,107,.14)",showlegend=False,hoverinfo="skip"))
        chart.add_trace(go.Scatter(
            x=xs,y=chosen,mode="lines",line=dict(color=accent,width=3.7),
            name="Planned (if all buys fill)" if planned_view else "Shares owned now",
            hovertemplate="Stock price ₹%{x:,.2f}<br>Expiry P&L ₹%{y:,.0f}<extra></extra>"))
        if show_both:
            other=actual if planned_view else planned
            chart.add_trace(go.Scatter(
                x=xs,y=other,mode="lines",line=dict(
                    color="#a8a1fd" if planned_view else "#41d6ab",
                    width=2.1,dash="dash"),
                name="Current expiry" if planned_view else "Planned expiry (conditional)",
                hovertemplate="Stock price ₹%{x:,.2f}<br>Expiry P&L ₹%{y:,.0f}<extra></extra>"))
        if show_today:
            modeled=[float(theoretical_today_payoff(position,pv,done,max(x,.01),iv,dte)) for x in xs]
            chart.add_trace(go.Scatter(
                x=xs,y=modeled,mode="lines",
                line=dict(color="#e7b16d",width=1.8,dash="dot"),
                name="Theoretical today",
                hovertemplate="Stock ₹%{x:,.2f}<br>Model P&L ₹%{y:,.0f}<extra></extra>"))

        chart.add_hline(y=0,line_color="#738298",line_width=1.1)
        chart.add_vline(x=float(spot),line_color="#81b7ed",line_dash="dot")
        chart.add_vline(x=float(strike),line_color="#dcae70",line_dash="dot")
        if show_target:
            chart.add_hline(y=float(target),line_color="#dcae70",line_dash="dash")
        chart.update_layout(
            template="plotly_dark",height=425,
            margin=dict(l=8,r=8,t=16,b=7),
            paper_bgcolor="#111a27",plot_bgcolor="#111a27",
            font=dict(color="#9faec1"),
            showlegend=bool(show_both or show_today),
            legend=dict(orientation="h",y=1.07,x=0,font=dict(size=11)),
            xaxis_title="Stock price (₹)", yaxis_title="Combined expiry P&L (₹)",
            hovermode="x unified",
        )
        chart.update_xaxes(gridcolor="#253345",zeroline=False)
        chart.update_yaxes(gridcolor="#253345",zeroline=False,tickformat=",")
        st.plotly_chart(chart,use_container_width=True)
        st.caption(
            f"Spot ₹{spot:,.2f} (blue marker) · Short call strike ₹{strike:,.2f} "
            "(gold marker) · Green shading = profit, red = loss. "
            + ("**Planned view assumes all future equity buys fill.**"
               if planned_view else "**Current view assumes no pending equity buys fill.**")
        )
        st.caption(
            "Current closeout P&L in the cards uses manually entered stock BID "
            "and option ASK. The chart shows hypothetical expiry outcomes, not a live price forecast."
        )

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
