"""Covered Call Strategy Builder: read-only Windows strategy and F&O screener.

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
from dhan_visual.table_layout import dataframe_key
from dhan_visual.payoff import current_expiry_payoff, expiry_breakevens, theoretical_today_payoff

st.set_page_config(page_title="Covered Call Strategy Builder", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
# Presentation only: shared terminal palette and responsive native controls.
from dhan_visual.terminal_theme import apply_terminal_theme
from dhan_visual.workspace_ui import initialize, remember_pref, render_settings
initialize()
if "ccs_dark_mode" not in st.session_state:
    st.session_state["ccs_dark_mode"] = True
apply_terminal_theme(st.session_state["ccs_dark_mode"])

st.markdown(
    """<section class="ps-header" aria-label="Covered Call Strategy Builder workspace">
      <div class="ps-topbar">
        <div class="ps-brand"><div class="ps-mark">↗</div>
          <h1>CCS Terminal <span>· v1.0</span></h1></div>
        <div class="ps-chips">
          <span class="ps-chip safe">● Market-data only</span>
          <span class="ps-chip">Stock F&O</span>
          <span class="ps-chip">Strategy demo</span>
        </div>
      </div>
      <p class="ps-sub">NSE F&O workspace · Read-only market data · Strategy demo</p>
    </section>""", unsafe_allow_html=True
)

# Keep the mode control readily accessible on desktop and iPhone.
theme_space, theme_action = st.columns([6, 1.4])
with theme_action:
    if st.button(
        "☀ Light mode" if st.session_state["ccs_dark_mode"] else "☾ Dark mode",
        key="ccs_theme_button", use_container_width=True,
        help="Toggle the local appearance; market data and scanner rules are unchanged.",
    ):
        st.session_state["ccs_dark_mode"] = not st.session_state["ccs_dark_mode"]
        remember_pref("dark",st.session_state["ccs_dark_mode"])
        st.rerun()

# Explicit single-active-broker policy; all tabs below use the same mode.
# Switching preserves saved Dhan/FYERS credentials without simultaneous logins.
from dhan_visual.broker_control import render_single_broker_selector
render_single_broker_selector()

# Default is a genuine lazy workspace: only the selected page executes.
# Legacy full-market tabs are available only by explicit opt-in for existing
# workflows; their higher request volume is clearly labeled.
with st.sidebar:
    legacy = st.toggle("Legacy full-market mode (high data usage)",
                       value=False, key="ccs_legacy_full_scan",
                       help="Off by default. When enabled, the old full-universe "
                            "scanner can download data from background tabs.")
    if legacy:
        st.warning("High data usage: full-universe scans may run in background.")

manage = None
if legacy:
    otm_home, search, manage, settings = st.tabs([
        "◎ OTM CE Home", "⌕ Market Screener", "▥ Manage Trade", "⚙ Settings",
    ])
    
    with otm_home:
        from dhan_visual.otm_home import render_otm_home
        render_otm_home()
    
    with search:
        # Preserve the original complete Page 1, now Page 2.
        # Full quote/technical jobs continue offscreen when Home is selected.
        from dhan_visual.screener_ui import render_screener
        render_screener()
    
    with settings:
        from dhan_visual.screener_ui import render_dhan_settings
        render_dhan_settings()
        render_settings()
else:
    from dhan_visual.selective_scan import render_selective_home, render_selective_market
    page = st.radio(
        "Workspace", ["◎ OTM CE Home", "⌕ Market Screener", "◇ Strategy Builder", "▥ Manage Trade", "⚙ Settings"],
        horizontal=True, key="ccs_selective_page", label_visibility="collapsed",
    )
    if page == "◎ OTM CE Home":
        render_selective_home()
    elif page == "⌕ Market Screener":
        render_selective_market()
    elif page == "◇ Strategy Builder":
        from dhan_visual.stock_strategy_ui import render_independent_strategy
        render_independent_strategy()
    elif page == "⚙ Settings":
        from dhan_visual.screener_ui import render_dhan_settings
        render_dhan_settings()
        render_settings()
    else:
        manage = st.container()

if manage is not None:
    with manage:
        st.info(
            "DEMO ONLY: Example positions and prices are simulated. "
            "Dhan market data, if connected, is read-only; no orders are placed."
        )
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
                    "Blue = current shares and short call · "
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
            accent="#18846a" if planned_view else "#2169c9"

            chart=go.Figure()
            chart.add_trace(go.Scatter(
                x=xs,y=[max(y,0) for y in chosen],mode="lines",
                line=dict(width=0),fill="tozeroy",
                fillcolor="rgba(33,155,113,.11)",showlegend=False,hoverinfo="skip"))
            chart.add_trace(go.Scatter(
                x=xs,y=[min(y,0) for y in chosen],mode="lines",
                line=dict(width=0),fill="tozeroy",
                fillcolor="rgba(216,73,87,.10)",showlegend=False,hoverinfo="skip"))
            chart.add_trace(go.Scatter(
                x=xs,y=chosen,mode="lines",line=dict(color=accent,width=3.7),
                name="Planned (if all buys fill)" if planned_view else "Shares owned now",
                hovertemplate="Stock price ₹%{x:,.2f}<br>Expiry P&L ₹%{y:,.0f}<extra></extra>"))
            if show_both:
                other=actual if planned_view else planned
                chart.add_trace(go.Scatter(
                    x=xs,y=other,mode="lines",line=dict(
                        color="#2169c9" if planned_view else "#18846a",
                        width=2.1,dash="dash"),
                    name="Current expiry" if planned_view else "Planned expiry (conditional)",
                    hovertemplate="Stock price ₹%{x:,.2f}<br>Expiry P&L ₹%{y:,.0f}<extra></extra>"))
            if show_today:
                modeled=[float(theoretical_today_payoff(position,pv,done,max(x,.01),iv,dte)) for x in xs]
                chart.add_trace(go.Scatter(
                    x=xs,y=modeled,mode="lines",
                    line=dict(color="#ac6f31",width=1.8,dash="dot"),
                    name="Theoretical today",
                    hovertemplate="Stock ₹%{x:,.2f}<br>Model P&L ₹%{y:,.0f}<extra></extra>"))

            chart.add_hline(y=0,line_color="#a0adbd",line_width=1.1)
            chart.add_vline(x=float(spot),line_color="#4a93dc",line_dash="dot")
            chart.add_vline(x=float(strike),line_color="#ca965d",line_dash="dot")
            if show_target:
                chart.add_hline(y=float(target),line_color="#ca965d",line_dash="dash")
            chart.update_layout(
                template="plotly_dark" if st.session_state.get("ccs_dark_mode") else "plotly_white",height=390,
                margin=dict(l=8,r=8,t=16,b=7),
                paper_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff",
                plot_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff",
                font=dict(color="#d0daeb" if st.session_state.get("ccs_dark_mode") else "#52627b"),
                showlegend=bool(show_both or show_today),
                legend=dict(orientation="h",y=1.07,x=0,font=dict(size=11)),
                xaxis_title="Stock price (₹)", yaxis_title="Combined expiry P&L (₹)",
                hovermode="x unified",
            )
            grid = "#34404e" if st.session_state.get("ccs_dark_mode") else "#edf0f5"
            chart.update_xaxes(gridcolor=grid,zeroline=False)
            chart.update_yaxes(gridcolor=grid,zeroline=False,tickformat=",")
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
            st.dataframe(
                staged, hide_index=True, use_container_width=True,
                key=dataframe_key("ccs_covered_call_stages", st.session_state),
            )
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

