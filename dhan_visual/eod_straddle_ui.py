"""Display free, historical ATM CE+PE straddles from ONE NSE F&O report.

No Dhan market-data entitlement, order access, implied-volatility estimates,
or synthetic LTPs. These are EOD closes, not executable bid/ask quotations.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .nse_eod_ce import NseEodSnapshot
from .table_layout import dataframe_key, autofit_column_config, autofit_enabled


def eod_straddle_rows(available, expiry: str,
                      snapshot: NseEodSnapshot | None) -> list[dict]:
    if snapshot is None:
        return []
    quotes = snapshot.straddles.get(expiry, {})
    rows = []
    for item in available:
        symbol = item.entry.symbol
        leg = quotes.get(symbol)
        lot = item.entry.lots.get(expiry)
        if leg is None or not lot or lot <= 0:
            continue
        rows.append({
            "Stock": symbol,
            "Expiry": leg.expiry,
            "ATM strike (₹)": float(leg.strike),
            "Call close (₹)": float(leg.call_close),
            "Put close (₹)": float(leg.put_close),
            "Combined (₹)": float(leg.combined_close),
            "Straddle (%)": float(leg.premium_pct),
            "Gross premium × lot (₹)": float(leg.combined_close * lot),
            "CE volume": leg.call_volume,
            "PE volume": leg.put_volume,
            "Liquidity": ("No trades in a leg"
                          if not leg.call_volume or not leg.put_volume
                          else "Both legs traded"),
        })
    return sorted(rows, key=lambda row: (-row["Straddle (%)"], row["Stock"]))


def render_eod_straddles(available, expiry: str,
                         snapshot: NseEodSnapshot | None) -> None:
    st.markdown("#### ATM straddles · free NSE closing data")
    if snapshot is None:
        st.caption("Import one official NSE F&O daily report to see historical "
                   "ATM CE + PE closing premiums. No Dhan plan required.")
        return
    rows = eod_straddle_rows(available, expiry, snapshot)
    st.caption(
        f"NSE F&O EOD {snapshot.report_date} · "
        f"{len(rows)} valid same-strike CE + PE pairs · "
        "historical closing prices only, not current LTP or sellable bid."
    )
    if not rows:
        st.info("The imported report has no complete positive-close ATM "
                f"straddle pairs for {expiry}. No prices were estimated.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame, hide_index=True, use_container_width=True, height=360,
        key=dataframe_key("ccs_eod_atm_straddles", st.session_state),
        column_config=autofit_column_config({
            "Straddle (%)": st.column_config.NumberColumn(
                "ATM straddle (%)", format="%.2f%%",
                help="(NSE CE close + NSE PE close) / NSE underlying close ×100"),
            "Gross premium × lot (₹)": st.column_config.NumberColumn(
                "Historical gross × lot (₹)", format="₹%.0f",
                help="No brokerage, slippage, margin, or delivery cost included."),
        }, autofit_enabled(st.session_state)),
    )
    st.caption("Zero-volume legs are marked as untraded references; "
               "use actual broker bid/ask and live lot specifications before "
               "placing any order.")
