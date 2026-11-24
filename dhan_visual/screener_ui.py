"""Read-only Dhan stock-F&O contract screener. No order endpoints exist here."""
import csv
import io
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from dhan_visual.dhan_market import (
    DHAN_MASTER_URL, DhanDataError, fetch_dhan_ltp, fetch_dhan_master,
    parse_dhan_master,
)
from dhan_visual.screener import choose_expiry, expiry_options, screen_rows
from dhan_visual.fast_universe import load_fast_catalogue


def _cached_dhan_master(refresh=False, progress=None):
    # A compact validated on-disk universe survives Streamlit/python restarts.
    # Full Dhan instrument master is NOT downloaded on ordinary screen visits.
    return load_fast_catalogue(refresh=refresh, progress=progress)


def _handle_master():
    if st.button("Load stock F&O universe from Dhan", type="primary", key="dhan_load_catalogue"):
        st.session_state["dhan_screener_loaded"] = True
    if not st.session_state.get("dhan_screener_loaded"):
        st.info(
            "Click **Load stock F&O universe from Dhan** to download Dhan's "
            "official instrument master. No client ID or access token is required "
            "to load the public list. Prices are requested separately."
        )
        return None, None

    force = st.button("Force re-download instrument list", key="dhan_refresh_master")
    if force:
        st.session_state.pop("dhan_price_snapshot", None)
    try:
        progress = st.empty()
        with st.spinner("Loading fast F&O universe (cache → small files → segment fallback)..."):
            universe, stamp = _cached_dhan_master(
                refresh=force, progress=lambda message: progress.caption(message)
            )
        progress.empty()
        return universe, stamp
    except Exception:
        st.warning(
            "Dhan's public instrument master could not be downloaded or verified. "
            "You can download the detailed CSV from Dhan and upload it below. "
            "No missing security IDs or lot sizes will be guessed."
        )
        st.markdown(f"[Download detailed Dhan instrument master]({DHAN_MASTER_URL})")
        uploaded = st.file_uploader("Upload detailed Dhan instrument master CSV",
                                    type=["csv"], key="dhan_upload_master")
        if uploaded is None:
            return None, None
        try:
            universe = parse_dhan_master(uploaded.getvalue())
            if len(universe) < 100:
                st.warning(f"Uploaded master has only {len(universe)} verified underlyings. Check its completeness.")
            return universe, "Manually uploaded Dhan CSV (provenance not independently verified)"
        except DhanDataError as exc:
            st.error(f"The uploaded Dhan master cannot be used: {exc}")
            return None, None


def _credential_input():
    with st.expander("Dhan market-data credentials · entered locally only", expanded=False):
        st.caption(
            "Enter your Dhan Client ID and current DhanHQ access token **here on "
            "your own laptop**, never in ChatGPT or GitHub. This preview sends them "
            "only to Dhan's documented marketfeed/ltp endpoint over HTTPS. "
            "They remain in this local Streamlit session; no token is saved to the repository."
        )
        cid = st.text_input("Dhan Client ID", value=os.environ.get("DHAN_CLIENT_ID", ""),
                            key="dhan_local_cid")
        token = st.text_input("DhanHQ access token", value=os.environ.get("DHAN_ACCESS_TOKEN", ""),
                              type="password", key="dhan_local_access_token")
        st.caption(
            "You can also set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN as local "
            "environment variables before starting Streamlit. Do not reuse "
            "ResultBot's production VM credentials or files."
        )
    return cid, token


def render_screener():
    st.subheader("NSE Stock F&O · Contract Value Screener")
    st.caption(
        "Dhan broker instrument master + Dhan NSE_EQ market-data snapshots. "
        "Lot sizes are expiry-specific. Contract value = Stock LTP × F&O lot. "
        "Read-only: no broker trading or ResultBot connection."
    )
    universe, source = _handle_master()
    if universe is None:
        return
    entries = [item.entry for item in universe]
    months = expiry_options(entries)
    expiry = st.selectbox(
        "F&O expiry month", months, index=months.index(choose_expiry(entries)),
        key="dhan_fno_expiry",
    )
    available = [item for item in universe if expiry in item.entry.lots]
    st.caption(f"**Universe:** {source} · **Dhan API quote rate:** maximum 1 request/second.")
    cid, token = _credential_input()
    previous_cid = st.session_state.get("dhan_snapshot_cid")
    if previous_cid is not None and previous_cid != cid.strip():
        st.session_state.pop("dhan_price_snapshot", None)
    a, b = st.columns([1, 1.7])
    mode = b.selectbox(
        "Price-loading mode",
        ["All stocks (complete scan, usually one request)",
         "Next 100 unpriced stocks (quick patch)",
         "Next 250 unpriced stocks"],
        key="dhan_quote_patch_mode",
        help="All stocks is fastest for a complete contract-size ranking. Patches help diagnose slow networks. Dhan permits 1000 instruments per request.",
    )
    refresh = a.button(
        "Fetch / refresh Dhan prices", key="dhan_refresh_ltps", type="primary",
        help="One manual NSE_EQ market-data snapshot; never place orders.",
    )
    st.caption(
        "Dhan's market-data API supports 1,000 instrument IDs per request. "
        "A complete stock F&O screen typically needs one quote request. "
        "Patch mode retrieves only the next missing symbols for troubleshooting."
    )
    if refresh:
        if not cid.strip() or not token.strip():
            st.error("Enter your own Dhan Client ID and access token in the credentials panel.")
        else:
            prior = st.session_state.get("dhan_price_snapshot") or {}
            prior_ids = prior.get("ids", {}) if st.session_state.get("dhan_snapshot_cid") == cid.strip() else {}
            prior_quotes = prior.get("quotes", {}) if st.session_state.get("dhan_snapshot_cid") == cid.strip() else {}
            if mode.startswith("All"):
                requested = available
                preserve = False
            else:
                missing = [x for x in available if
                           x.entry.symbol not in prior_quotes or
                           prior_ids.get(x.entry.symbol) != x.equity_security_id]
                size = 100 if "100" in mode else 250
                requested = missing[:size]
                preserve = True
            if not requested:
                st.info("All selected stocks already have a snapshot. Use the All mode to refresh them.")
            else:
                with st.spinner(f"Requesting {len(requested)} NSE_EQ prices from Dhan..."):
                    try:
                        new_prices, errors = fetch_dhan_ltp(requested, cid, token)
                        merged = dict(prior_quotes) if preserve else {}
                        merged.update(new_prices)
                        ids = dict(prior_ids) if preserve else {}
                        ids.update({x.entry.symbol: x.equity_security_id for x in requested})
                        st.session_state["dhan_price_snapshot"] = {
                            "quotes": merged,
                            "errors": errors,
                            "request_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                            "ids": ids,
                        }
                        st.session_state["dhan_snapshot_cid"] = cid.strip()
                        if errors:
                            st.warning(f"{len(errors)} Dhan quote request errors; old/missing quotes are not fabricated.")
                    except DhanDataError as exc:
                        st.error(f"Dhan data error: {exc}")

    snapshot = st.session_state.get("dhan_price_snapshot") or {}
    raw_prices = snapshot.get("quotes", {})
    snapshot_ids = snapshot.get("ids", {})
    prices = {item.entry.symbol: raw_prices[item.entry.symbol] for item in available
              if item.entry.symbol in raw_prices
              and snapshot_ids.get(item.entry.symbol) == item.equity_security_id}
    errors = snapshot.get("errors", [])
    if errors:
        st.warning(
            f"Dhan quote retrieval had {len(errors)} errors. Missing quotes remain "
            f"unknown; nothing is replaced by Yahoo: {'; '.join(errors[:3])}"
        )
    if snapshot.get("request_at"):
        st.caption(
            f"**Dhan quote request finished:** {snapshot['request_at']} · "
            "Quote age is measured from the request, NOT the exchange's actual "
            "last-trade time. Use Refresh to get a new market-data snapshot."
        )
    else:
        st.info("The complete symbol/lot list is loaded. Add credentials and fetch Dhan LTPs to calculate contract values.")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Stock F&O underlyings", f"{len(available):,}")
    k2.metric("Dhan prices available", f"{len(prices):,}")
    k3.metric("Missing prices", f"{len(available) - len(prices):,}")
    k4.metric("Selected expiry", expiry)
    if len(prices) < len(available):
        st.warning(
            f"Partial price coverage: {len(prices)}/{len(available)} stock prices. "
            "Contract-value sorting and min/max filters are complete ONLY for "
            "the priced rows. Use All stocks to scan the full list."
        )

    st.markdown("#### Filter by stock and contract value")
    a, b, c = st.columns([1.4, 1.2, 1.5])
    query = a.text_input("Stock symbol or company", placeholder="RELIANCE, TCS, INFY...", key="fno_symbol_find")
    order = b.radio(
        "Sort estimated contract value", ["Low → high", "High → low"],
        horizontal=True, key="fno_value_sort",
    )
    quick = c.selectbox(
        "Quick contract value filter",
        ["Custom", "Below ₹5 lakh", "Below ₹6 lakh", "Below ₹7 lakh",
         "Below ₹8 lakh", "Above ₹5 lakh", "Above ₹8 lakh"], key="fno_value_preset",
    )
    a, b = st.columns(2)
    min_l = a.number_input("Minimum value (₹ lakh)", min_value=0.0, step=0.5, value=0.0, key="fno_min_lakh")
    max_l = b.number_input("Maximum value (₹ lakh; 0 = no upper limit)",
                            min_value=0.0, step=0.5, value=0.0, key="fno_max_lakh")
    presets = {
        "Below ₹5 lakh": (0, 5), "Below ₹6 lakh": (0, 6),
        "Below ₹7 lakh": (0, 7), "Below ₹8 lakh": (0, 8),
        "Above ₹5 lakh": (5, 0), "Above ₹8 lakh": (8, 0),
    }
    low, high = presets.get(quick, (min_l, max_l))
    try:
        selected = screen_rows(
            [x.entry for x in available], expiry, prices, query,
            min_value=low * 100000, max_value=high * 100000,
            descending=order == "High → low",
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    if not selected:
        st.info("No available quotes or underlyings match this filter. Widen the range or refresh Dhan LTPs.")
        return
    st.markdown(f"**{len(selected)} matching stocks** · "
                f"{sum(row['Last price (₹)'] is not None for row in selected)} with a Dhan LTP")
    st.caption(
        "**Contract value** is underlying notional, not required broker margin or option premium. "
        "Prices absent from Dhan are shown as N/A and excluded from numerical contract-value filters."
    )
    df = pd.DataFrame(selected)
    st.dataframe(
        df, use_container_width=True, hide_index=True, height=620,
        column_config={
            "Symbol": st.column_config.TextColumn("Stock"),
            "Company": st.column_config.TextColumn("Underlying", width="medium"),
            "Last price (₹)": st.column_config.NumberColumn("Dhan LTP (₹)", format="₹%.2f"),
            "Lot size": st.column_config.NumberColumn("Lot", format="%d"),
            "Contract value (₹)": st.column_config.NumberColumn("Contract value (₹)", format="₹%.2f"),
            "Quote time": st.column_config.TextColumn("Snapshot request (UTC)", width="medium"),
            "Price source": st.column_config.TextColumn("Price source"),
        },
    )
    buff = io.StringIO()
    writer = csv.DictWriter(buff, fieldnames=list(selected[0].keys()))
    writer.writeheader()
    writer.writerows(selected)
    st.download_button(
        "Export filtered results to CSV", data=buff.getvalue(),
        file_name=f"dhan_fno_contract_value_{expiry}.csv", mime="text/csv",
        key="dhan_export_contracts",
    )
