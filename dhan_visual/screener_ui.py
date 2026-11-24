"""Read-only, on-demand NSE F&O contract-value screening interface."""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

from dhan_visual.screener import (
    NSE_LOTS_URL, choose_expiry, expiry_options, fetch_nse_catalogue,
    fetch_yahoo_quotes, parse_nse_lots, screen_rows,
)


@st.cache_data(ttl=4 * 60 * 60, show_spinner=False)
def _nse_lots_cached():
    return fetch_nse_catalogue()


@st.cache_data(ttl=15 * 60, show_spinner=False)
def _yahoo_cached(symbols: tuple[str, ...]):
    return fetch_yahoo_quotes(list(symbols))


def render_screener():
    st.subheader("NSE Stock F&O · Contract Value Screener")
    st.caption(
        "All available stock F&O symbols and applicable lot sizes from NSE. "
        "Indicative Yahoo Finance bar prices are free, but NOT broker LTPs and "
        "may be delayed or unavailable. No broker access or trading actions."
    )

    if st.button("Load NSE F&O stock list + indicative prices", type="primary",
                 key="fno_load"):
        st.session_state["fno_screener_loaded"] = True

    if not st.session_state.get("fno_screener_loaded", False):
        st.info(
            "Click **Load NSE F&O stock list + indicative prices** to fetch the "
            "exchange's complete published stock-lot table and available Yahoo prices. "
            "Nothing is fetched automatically on other tabs."
        )
        st.markdown(
            "You will be able to search symbols, select expiry, view "
            "**Price × Lot size = Estimated contract value**, and filter "
            "by a chosen investment threshold."
        )
        return

    a, b = st.columns(2)
    with a:
        if st.button("Refresh NSE lot-size file", key="fno_refresh_lots"):
            _nse_lots_cached.clear()
    with b:
        if st.button("Refresh Yahoo prices", key="fno_refresh_quotes"):
            _yahoo_cached.clear()

    source = "NSE permitted lot-size file"
    try:
        with st.spinner("Loading official NSE stock lot sizes..."):
            catalogue, fetched_at = _nse_lots_cached()
    except Exception as exc:
        st.warning(
            f"Cannot reach or validate NSE's lot-size CSV ({type(exc).__name__}). "
            "No lot sizes will be invented. Download the official NSE file and "
            "upload it below instead."
        )
        upload = st.file_uploader("Upload the latest NSE fo_mktlots.csv",
                                  type=["csv"], key="fno_official_upload")
        if upload is None:
            st.markdown(f"[Open NSE lot-size file]({NSE_LOTS_URL})")
            return
        try:
            catalogue = parse_nse_lots(upload.getvalue())
        except ValueError as error:
            st.error(f"Uploaded file could not be parsed: {error}")
            return
        fetched_at = f"User-uploaded CSV · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        source = "User-uploaded file, authenticity unverified"

    months = expiry_options(catalogue)
    nearest = choose_expiry(catalogue)
    expiry = st.selectbox(
        "Contract expiry month (lot sizes can differ by expiry)",
        months, index=months.index(nearest), key="fno_expiry_month",
    )
    available = [entry for entry in catalogue if expiry in entry.lots]
    if not available:
        st.error("The official file has no stock-lot entries for this expiry.")
        return

    with st.spinner(
        f"Fetching indicative Yahoo prices for {len(available)} symbols; "
        "free sources can throttle requests..."
    ):
        prices, errors = _yahoo_cached(tuple(sorted(entry.symbol for entry in available)))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stock F&O underlyings", f"{len(available):,}")
    c2.metric("Yahoo prices available", f"{len(prices):,}")
    c3.metric("Prices unavailable", f"{len(available) - len(prices):,}")
    c4.metric("Selected expiry", expiry)
    st.caption(
        f"**Lot source:** {source} · Checked {fetched_at} · "
        "**Price source:** Yahoo Finance, latest available 15-minute bar "
        "or daily close (not live LTP). Price cache ≤15 minutes."
    )
    if errors:
        st.warning(
            f"Yahoo returned {len(errors)} failed quote batches. Stocks with no price "
            "remain in the list but have no estimated contract value. "
            "Refresh later rather than treating missing prices as ₹0."
        )

    st.markdown("#### Search and filter")
    a, b, c = st.columns([1.6, 1.2, 1.4])
    query = a.text_input("Stock symbol or company", placeholder="RELIANCE, TCS, INFY…", key="fno_symbol_find")
    order = b.radio("Sort estimated contract value", ["Low → high", "High → low"],
                    horizontal=True, key="fno_value_sort")
    quick = c.selectbox(
        "Contract size preset",
        ["Custom", "Less than ₹5 lakh", "Less than ₹6 lakh",
         "Less than ₹7 lakh", "Less than ₹8 lakh",
         "More than ₹5 lakh", "More than ₹8 lakh"],
        key="fno_value_preset",
    )
    a, b = st.columns(2)
    minimum_lakh = a.number_input(
        "Minimum contract value (₹ lakh)", min_value=0.0, value=0.0,
        step=0.5, key="fno_min_lakh",
        help="0 means no lower limit. These fields apply when preset = Custom.",
    )
    maximum_lakh = b.number_input(
        "Maximum contract value (₹ lakh)", min_value=0.0, value=0.0,
        step=0.5, key="fno_max_lakh",
        help="0 means no upper limit. These fields apply when preset = Custom.",
    )
    limits = {
        "Less than ₹5 lakh": (0.0, 5.0),
        "Less than ₹6 lakh": (0.0, 6.0),
        "Less than ₹7 lakh": (0.0, 7.0),
        "Less than ₹8 lakh": (0.0, 8.0),
        "More than ₹5 lakh": (5.0, 0.0),
        "More than ₹8 lakh": (8.0, 0.0),
    }
    low_lakh, high_lakh = limits.get(quick, (minimum_lakh, maximum_lakh))
    try:
        selected = screen_rows(
            available, expiry, prices, query,
            min_value=low_lakh * 100000, max_value=high_lakh * 100000,
            descending=order == "High → low",
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    quoted = sum(row["Last price (₹)"] is not None for row in selected)
    st.markdown(f"**{len(selected):,} matching stocks** · {quoted:,} with indicative price")
    st.caption(
        "**Estimated contract value = Last available equity price × selected-expiry "
        "lot size.** This is the underlying notional value, not required option "
        "margin, option premium or guaranteed execution cost. Rows without "
        "prices are omitted from numeric-range filters."
    )

    if not selected:
        st.info("No stocks match this filter with an available price. Try a wider range.")
        return

    df = pd.DataFrame(selected)
    st.dataframe(
        df, use_container_width=True, hide_index=True, height=610,
        column_config={
            "Symbol": st.column_config.TextColumn("F&O stock", width="small"),
            "Company": st.column_config.TextColumn("Company", width="large"),
            "Last price (₹)": st.column_config.NumberColumn(
                "Last price (₹)", help="Yahoo Finance 15-minute bar or daily close; delayed/indicative",
                format="₹%.2f",
            ),
            "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
            "Contract value (₹)": st.column_config.NumberColumn(
                "Price × Lot (₹)", format="₹%.2f",
            ),
            "Quote time": st.column_config.TextColumn("Quote date/time", width="medium"),
            "Price source": st.column_config.TextColumn("Source", width="medium"),
        },
    )
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(selected[0].keys()))
    writer.writeheader()
    writer.writerows(selected)
    st.download_button(
        "Download filtered stocks as CSV",
        data=out.getvalue(), file_name=f"nse_fno_contract_values_{expiry}.csv",
        mime="text/csv", key="fno_export",
    )
