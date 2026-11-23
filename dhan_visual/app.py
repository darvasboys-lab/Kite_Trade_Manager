"""Windows-local, DEMO-only Dhan covered-call strategy chart dashboard.

No Dhan credentials, broker writes, service startup, or external network calls.
The chart is a UI preview, not account P&L or an executable order plan.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.graph_objects as go
import streamlit as st
from dhan_visual.strategy import Position, plan, current_closeout, expiry_payoff, exposure_after_fills

st.set_page_config(page_title="Covered Call | Strategy Studio", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.stApp {background: #0c1018; color: #e9eef5;}
.block-container {padding-top: 1.6rem; max-width: 1380px; padding-bottom: 3rem;}
h1, h2, h3 {letter-spacing: -0.035em !important;}
[data-testid="stMetric"] {background:#141b26; border:1px solid #283343; border-radius:15px; padding:18px 19px;}
[data-testid="stMetricLabel"] {color:#9caec4;}
[data-testid="stMetricValue"] {color:#f5f8fc;}
[data-testid="stTabs"] [data-baseweb="tab-list"] {gap:8px;}
[data-testid="stTabs"] button {background:#141b26; border-radius:10px 10px 0 0;}
[data-testid="stTabs"] button[aria-selected="true"] {color:#44c9b4; border-bottom: 2px solid #44c9b4;}
div.stAlert {border-radius: 12px;}
.hero {border-radius:20px; padding:26px 28px; background:linear-gradient(110deg,#142d35 0%,#131c2b 68%,#101723 100%);border:1px solid #2a4050;margin-bottom:14px;}
.eyebrow {font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#55d2c0;font-weight:700;}
.hero-title {font-size:35px;font-weight:780;letter-spacing:-.04em;margin:7px 0 5px;line-height:1.1;}
.hero-sub {color:#aebccc;font-size:14px;}
.tag {display:inline-block;padding:5px 9px;border-radius:8px;background:#24313e;color:#b7efea;font-size:11px;font-weight:700;margin:0 6px 0 0;}
.mini {color:#8798ab;font-size:12px;}
hr {border-color:#223042;}
</style>
""", unsafe_allow_html=True)

st.markdown('''<div class="hero"><div class="eyebrow">ResultBot technology · New independent strategy</div>
<div class="hero-title">Covered Call <span style="color:#67d6c7">Strategy Studio</span></div>
<div class="hero-sub">Search a stock. Discover your manual first batch. Plan the remaining equity buys. Track the entire equity + short-call outcome.</div>
<div style="margin-top:16px"><span class="tag">DEMO / SIMULATION</span><span class="tag">BUY-ONLY PLANNER</span><span class="tag">NO BROKER ORDERS</span></div></div>''', unsafe_allow_html=True)

search_tab, manage_tab = st.tabs(["⌕  Search Stock", "▥  Manage Trade & Strategy Charts"])

with search_tab:
    st.subheader("Stock discovery")
    st.caption("This is a local UI prototype. Stock lookup and live Dhan market data are not yet connected. No quote on this screen is live.")
    ticker = st.text_input("Search NSE F&O stock", placeholder="Enter a stock symbol to inspect after connecting Dhan", value="", key="ticker")
    a,b,c = st.columns(3)
    a.metric("Dhan connection", "Not connected")
    b.metric("Live stock price", "Not available")
    c.metric("Available calls / strikes", "Not available")
    if ticker.strip():
        st.info(f"{ticker.strip().upper()}: quote, option-chain and contract-size lookup will appear here when the read-only Dhan adapter is verified.")
    st.markdown("**Planned live discovery:** Dhan holdings + current-day positions + trade history + instrument master + timestamp-verified quote feed. Positions will require review before activation.")

with manage_tab:
    st.info("SIMULATION ONLY · Numbers below are editable sample inputs, NOT your Dhan holdings, live quotes, confirmed fills or trading instructions.", icon="🧪")
    with st.expander("Strategy assumptions and inputs", expanded=True):
        a,b,c,d = st.columns(4)
        manual_avg = a.number_input("Manual first-batch average (₹)", min_value=0.05, max_value=1000000.0, value=1000.0, step=1.0)
        manual_shares = b.number_input("Manual shares owned", min_value=1, max_value=1000000, value=100, step=1)
        strike = c.number_input("Sold CALL strike (₹)", min_value=0.05, max_value=1000000.0, value=1200.0, step=1.0)
        option_qty = d.number_input("Shares in short call(s)", min_value=1, max_value=1000000, value=500, step=1)
        e,f,g,h = st.columns(4)
        batch_count = e.selectbox("Automatic buying batches", list(range(1,21)), index=2)
        premium = f.number_input("Total call premium received (₹)", min_value=0.01, value=10000.0, step=100.0)
        tick = g.number_input("Stock tick size (₹)", min_value=0.01, value=0.05, format="%.2f", step=0.01)
        estimated_costs = h.number_input("Est. total expenses (₹)", min_value=0.0, value=0.0, step=50.0)

    try:
        position = Position(Decimal(str(manual_avg)), int(manual_shares), Decimal(str(strike)),
                            int(option_qty), Decimal(str(premium)), Decimal(str(tick)))
        result = plan(position, int(batch_count), estimated_costs)
    except ValueError as exc:
        st.error(f"Plan unavailable: {exc}. Please correct the illustrative inputs.")
        st.stop()

    st.markdown("### Strategy overview")
    a,b,c,d = st.columns(4)
    a.metric("Manual purchase average", f"₹{manual_avg:,.2f}")
    b.metric("Final buying trigger", f"₹{float(result.final_trigger):,.2f}", help="No closer than 0.5% below the sold call strike")
    c.metric("Shares left to accumulate", f"{result.remaining:,}")
    d.metric("Buying intervals", f"{batch_count} batches")

    st.markdown("### Market & strategy P&L scenario")
    a,b,c,d = st.columns(4)
    stock_bid = a.number_input("Illustrative stock BID / CMP (₹)", min_value=0.05, max_value=1000000.0, value=1100.0, step=1.0)
    call_ask = b.number_input("Illustrative call buyback ASK (₹/share)", min_value=0.0, max_value=1000000.0, value=18.0, step=1.0)
    filled = c.slider("Simulated completed automatic batches", min_value=0, max_value=len(result.stages), value=0)
    closing_costs = d.number_input("Estimated closeout expenses (₹)", min_value=0.0, value=0.0, step=50.0)
    owned, paid = exposure_after_fills(position, result, filled)
    remaining = option_qty - owned
    indicative_pnl = current_closeout(position, result, filled, stock_bid, call_ask, closing_costs)
    total_unfilled_at_trigger = sum(s.shares*s.trigger for s in result.stages[filled:])
    current_target = result.profit_target
    a,b,c,d = st.columns(4)
    a.metric("Indicative combined closeout P&L", f"₹{indicative_pnl:,.2f}", help="Simulated stock BID minus paid cash plus collected option premium minus option buyback ASK × short quantity minus costs")
    b.metric("Conditional payoff at strike", f"₹{result.projected_profit_at_strike:,.2f}", help="Hypothetical net payoff if all remaining purchases fill at planned trigger prices and call reaches expiry")
    c.metric("50% profit threshold", f"₹{current_target:,.2f}")
    d.metric("Additional cash at planned triggers", f"₹{total_unfilled_at_trigger:,.2f}")
    if remaining > 0:
        st.warning(f"Coverage gap: {remaining:,} shares remain against the short call. The position is NOT fully covered and can have substantial losses or delivery obligations. Simulation is not an execution guarantee.")
    else:
        st.success("The simulated equity share count matches the short-call quantity. Confirm actual positions separately with Dhan.")
    if indicative_pnl >= current_target:
        st.info("DEMO 50% TARGET CROSSED · This is not a live trade alert. Actual profit booking remains manual and requires verified quotes, fills and costs.")

    st.markdown("### Strategy price chart")
    st.caption("Step curve shows total shares AFTER simulated fill at each trigger. Dashed spot line shows the editable illustrative stock price, not a live feed.")
    steps = [(float(position.manual_avg), manual_shares)]
    cumulative = manual_shares
    for stage in result.stages:
        cumulative += stage.shares
        steps.append((float(stage.trigger), cumulative))
    x = [p[0] for p in steps]
    y = [p[1] for p in steps]
    ladder_chart = go.Figure()
    ladder_chart.add_trace(go.Scatter(x=x, y=y, mode="lines+markers+text", line=dict(color="#59d5be", width=3, shape="hv"),
             marker=dict(size=10, color="#8ae7d6"), text=["Manual"]+[f"B{i}" for i in range(1,len(steps))], textposition="top center", name="Projected shares"))
    ladder_chart.add_hline(y=option_qty, line_dash="dot", line_color="#7e91af", annotation_text="Call lot coverage")
    ladder_chart.add_vline(x=float(strike), line_dash="dot", line_color="#e6a85c", annotation_text="Call strike")
    ladder_chart.add_vline(x=float(stock_bid), line_dash="dash", line_color="#9ea7fa", annotation_text="Illustrative CMP")
    ladder_chart.update_layout(template="plotly_dark", paper_bgcolor="#101824", plot_bgcolor="#101824", height=390,
             margin=dict(l=12,r=12,b=16,t=35), xaxis_title="Underlying stock price (₹)", yaxis_title="Shares held after stage fill",
             showlegend=False, font=dict(color="#aebfd0"), hovermode="x unified")
    st.plotly_chart(ladder_chart, use_container_width=True)

    st.markdown("### Conditional expiry profit profile")
    st.caption("Assumes ALL planned equity batches fill at their triggers and the short call is held to expiry. Not a prediction of stock price or today's option value.")
    lo = max(float(position.manual_avg)*0.75, float(position.strike)*0.6)
    hi = max(float(position.strike)*1.25, float(position.manual_avg)*1.3)
    px = [lo+(hi-lo)*i/120 for i in range(121)]
    py = [float(expiry_payoff(position, result, q, estimated_costs)) for q in px]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=px, y=py, mode="lines", name="Conditional P&L at expiry", line=dict(color="#59d5be",width=3), fill="tozeroy", fillcolor="rgba(89,213,190,0.09)"))
    fig.add_hline(y=0, line_color="#6c778b", line_width=1)
    fig.add_hline(y=float(current_target), line_dash="dash", line_color="#f0b66d", annotation_text="50% profit target")
    fig.add_vline(x=float(strike), line_dash="dot", line_color="#e6a85c", annotation_text="Call strike")
    fig.update_layout(template="plotly_dark", paper_bgcolor="#101824", plot_bgcolor="#101824", height=370,
                      margin=dict(l=12,r=12,b=16,t=35), xaxis_title="Hypothetical expiry stock price (₹)",
                      yaxis_title="Strategy payoff (₹)", showlegend=False, font=dict(color="#aebfd0"), hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Complete buying plan")
    stages = [{"Batch":"Initial · Manual", "Trigger price (₹)": f"{manual_avg:,.2f}",
               "Shares":manual_shares,"Est. purchase (₹)":f"{manual_avg*manual_shares:,.2f}","Simulated status":"Already purchased"}]
    for stage in result.stages:
        stages.append({"Batch":f"Auto {stage.index}", "Trigger price (₹)":f"{stage.trigger:,.2f}",
             "Shares":stage.shares,"Est. purchase (₹)":f"{stage.trigger*stage.shares:,.2f}",
             "Simulated status":"Filled (demo)" if stage.index <= filled else "Planned (demo)"})
    st.dataframe(stages, use_container_width=True, hide_index=True)
    a,b,c = st.columns(3)
    a.metric("Simulated equity shares", f"{owned:,} / {option_qty:,}")
    b.metric("Simulated equity invested", f"₹{paid:,.2f}")
    c.metric("Reference price gap / batch", f"₹{result.price_interval:,.2f}")
    st.caption("No order buttons are present in this preview. Live Dhan integration will require account-specific identity checks, tradebook reconciliation, durable per-batch fills, real-time source timestamps, execution safety gates, and explicit activation.")
