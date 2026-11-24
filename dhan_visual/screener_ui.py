"""Automatic F&O list + Dhan/Yahoo read-only price sources.

No file upload, no order endpoints, no exchange-credential persistence.
F&O list starts fetching in a worker and survives restarts as public-only cache.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import csv
import io
import os

import pandas as pd
import streamlit as st

from dhan_visual.dhan_market import DhanDataError, DhanUnderlying, fetch_dhan_ltp
from dhan_visual.fast_universe import load_fast_catalogue, resolve_equity_ids, match_cash_ids, _write_cache
from dhan_visual.screener import choose_expiry, expiry_options, fetch_yahoo_quotes, screen_rows


@st.cache_resource
def _universe_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="stock-fno-list")


def _start_universe(force=False):
    """Start exactly one network/cache load; no Streamlit API inside worker."""
    current = st.session_state.get("fno_universe_future")
    if current is not None and not current.done():
        return
    st.session_state.pop("fno_universe_error", None)
    st.session_state["fno_universe_future"] = _universe_pool().submit(
        load_fast_catalogue, refresh=force
    )


@st.fragment(run_every="2s")
def _load_status():
    """Refresh loading status without blocking the rest of Streamlit."""
    future = st.session_state.get("fno_universe_future")
    if future is None:
        st.caption("Preparing automatic instrument download...")
        return
    if not future.done():
        st.info(
            "Loading the NSE stock F&O list automatically. The dashboard remains usable; "
            "no CSV download or upload is necessary."
        )
        return
    try:
        catalogue, provenance = future.result()
        st.session_state["fno_universe_data"] = (catalogue, provenance)
        st.session_state.pop("fno_universe_error", None)
    except Exception as exc:
        st.session_state["fno_universe_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
    finally:
        st.session_state.pop("fno_universe_future", None)
    st.rerun()


def _auto_universe():
    """Render immediately from memory, otherwise transparently fetch/retry."""
    data = st.session_state.get("fno_universe_data")
    future = st.session_state.get("fno_universe_future")
    error = st.session_state.get("fno_universe_error")
    if data:
        a, b = st.columns([4, 1])
        a.caption(f"**F&O list:** {data[1]}")
        if b.button("Refresh list", key="fno_refresh_universe"):
            _start_universe(force=True)
            st.rerun()
        if future:
            _load_status()
        elif error:
            st.warning("Could not refresh the list; displaying last successfully verified stocks.")
        return data[0]
    if future is not None:
        _load_status()
        return None
    if error:
        st.error(
            "The automatic NSE/Dhan downloads could not be completed. No file upload "
            "is required. Check internet access to Dhan/NSE and retry."
        )
        with st.expander("Technical details", expanded=False):
            st.code(error)
        if st.button("Retry automatic loading", key="fno_retry_load"):
            _start_universe(force=True)
            st.rerun()
        return None
    _start_universe()
    _load_status()
    return None


@st.cache_data(ttl=15 * 60, show_spinner=False)
def _cached_yahoo(symbols):
    # The Yahoo source is user-selected and never mixed with Dhan LTP.
    return fetch_yahoo_quotes(list(symbols))


def _dhan_credentials():
    with st.expander("Dhan market-data login · enter once per local session",
                     expanded=not bool(os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"))):
        st.caption(
            "Enter credentials only on your own laptop, never in ChatGPT or GitHub. "
            "Only the Dhan marketfeed/ltp API is contacted. Neither credential "
            "is written into the stock catalogue cache."
        )
        cid = st.text_input("Dhan Client ID", value=os.environ.get("DHAN_CLIENT_ID", ""),
                            key="dhan_local_cid")
        token = st.text_input("Dhan access token", value=os.environ.get("DHAN_ACCESS_TOKEN", ""),
                              type="password", key="dhan_local_access_token")
    return cid, token


def _fetch_prices(source, available):
    st.markdown("#### Market prices")
    a, b = st.columns([2, 1])
    with a:
        provider = st.radio(
            "Price source", ["Dhan", "Yahoo Finance"], index=0,
            horizontal=True, key="fno_price_provider",
            help="Dhan is the default. Yahoo is a manually selected backup and "
                 "may return delayed prices or rate-limit requests.",
        )
    with b:
        if st.button("Refresh prices", key="fno_refresh_prices", type="primary"):
            st.session_state["fno_refresh_requested"] = True

    symbols = tuple(sorted(item.entry.symbol for item in available))
    if provider == "Dhan":
        cid, token = _dhan_credentials()
        cid, token = cid.strip(), token.strip()
        credentials_ready = bool(cid and len(token) >= 16)
        fingerprint = sha256((cid + ":" + token).encode()).hexdigest() if credentials_ready else None
        stored = st.session_state.get("fno_dhan_snapshot") or {}
        if stored.get("fingerprint") != fingerprint:
            st.session_state.pop("fno_dhan_snapshot", None)
            stored = {}

        control = st.selectbox(
            "Price request", ["All F&O stocks", "Next 100 missing", "Next 250 missing"],
            key="fno_dhan_batch",
            help="Dhan permits up to 1000 stock security IDs in one LTP snapshot.",
        )
        manual_refresh = st.session_state.pop("fno_refresh_requested", False)
        repair_ids = st.button("Recheck Dhan security IDs", key="fno_repair_cash_ids")
        cached_ids = st.session_state.get("fno_resolved_cash_ids") or {}
        verified = [
            DhanUnderlying(x.entry, cached_ids.get(x.entry.symbol, x.equity_security_id))
            for x in available
        ]
        missing_ids = sum(x.equity_security_id <= 0 for x in verified)

        if not credentials_ready:
            st.info(
                "Stock list is ready, but Dhan prices require a **Dhan Client ID and "
                "current DhanHQ access token** in the login panel above. "
                "An active subscription doesn't log this local app in automatically. "
                "Yahoo Finance is available as a backup."
            )
        else:
            # One automatic request after credentials are entered/changed.
            # Failure is remembered to avoid a retry on every Streamlit rerun.
            auto_first = st.session_state.get("fno_dhan_auto_attempt") != fingerprint
            run_fetch = bool(manual_refresh or auto_first or repair_ids)
            if run_fetch:
                st.session_state["fno_dhan_auto_attempt"] = fingerprint
                if missing_ids or repair_ids:
                    with st.spinner("Matching official NSE cash security IDs (read-only)..."):
                        try:
                            ids = resolve_equity_ids()
                            updated, matched = match_cash_ids(available, ids)
                            if matched == 0:
                                raise DhanDataError(
                                    "No F&O symbols matched Dhan's NSE_EQ security IDs; "
                                    "check the current Dhan compact/detailed master schema."
                                )
                            cached_ids = ids
                            st.session_state["fno_resolved_cash_ids"] = ids
                            verified = updated
                            missing_ids = len(verified) - matched

                            # Public metadata only; no broker token or quote data.
                            previous_data = st.session_state.get("fno_universe_data")
                            if previous_data:
                                entries, _label = previous_data
                                refreshed, _ = match_cash_ids(entries, ids)
                                label = "NSE official F&O lots + Dhan NSE_EQ IDs"
                                _write_cache(refreshed, label)
                                st.session_state["fno_universe_data"] = (refreshed, label)
                        except DhanDataError as exc:
                            st.error(
                                f"Cannot map Dhan stock IDs: {exc}. The official stock "
                                "list remains available; try Yahoo backup."
                            )

                prior_ids = stored.get("ids", {})
                prior_quotes = stored.get("quotes", {})
                eligible = [x for x in verified if x.equity_security_id > 0]
                if control.startswith("All"):
                    requested, carry = eligible, False
                else:
                    remaining = [
                        x for x in eligible
                        if x.entry.symbol not in prior_quotes or
                        prior_ids.get(x.entry.symbol) != x.equity_security_id
                    ]
                    requested = remaining[:100 if "100" in control else 250]
                    carry = True
                if not eligible:
                    st.error(
                        "No verified NSE_EQ stock security IDs. Prices cannot be "
                        "calculated from Dhan until mapping succeeds."
                    )
                elif requested:
                    with st.spinner(f"Fetching Dhan stock LTPs for {len(requested)} symbols..."):
                        try:
                            new_quotes, errors = fetch_dhan_ltp(requested, cid, token)
                        except DhanDataError as exc:
                            new_quotes, errors = {}, [str(exc)]
                    quote_map = dict(prior_quotes) if carry else {}
                    quote_map.update(new_quotes)
                    id_map = dict(prior_ids) if carry else {}
                    id_map.update({x.entry.symbol: x.equity_security_id for x in requested})
                    stored = {
                        "quotes": quote_map, "ids": id_map, "errors": errors,
                        "fingerprint": fingerprint,
                        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    }
                    st.session_state["fno_dhan_snapshot"] = stored
                else:
                    st.info("All matching stock prices are already loaded. Choose All F&O stocks to refresh.")

        # Display a real snapshot only when its broker ID matches the selected stock.
        prices = {
            row.entry.symbol: stored.get("quotes", {})[row.entry.symbol]
            for row in verified
            if row.entry.symbol in stored.get("quotes", {})
            and stored.get("ids", {}).get(row.entry.symbol) == row.equity_security_id
        }
        if credentials_ready:
            st.caption(
                f"Verified Dhan NSE_EQ IDs: {len(verified)-missing_ids:,}/{len(verified):,} · "
                f"Dhan stock prices: {len(prices):,}/{len(verified):,} · "
                f"Last requested: {stored.get('requested_at', 'not yet')}. "
                "LTP snapshot time is not the exchange's actual last-trade timestamp."
            )
        errors = stored.get("errors", [])
        if credentials_ready and not prices and stored.get("requested_at") and not errors:
            st.error("Dhan returned no usable stock prices. Recheck Dhan security IDs or use Yahoo backup.")
    else:
        st.caption(
            "Yahoo Finance backup: free historical bar closes; prices may be delayed, "
            "stale or unavailable. These quotes are never combined with Dhan quotes."
        )
        size = st.selectbox(
            "Yahoo batch", ["First 50 stocks", "First 100 stocks", "All stocks (slow)"],
            key="fno_yahoo_batch",
        )
        cache = st.session_state.get("fno_yahoo_snapshot") or {}
        if st.session_state.pop("fno_refresh_requested", False):
            target = symbols if size.startswith("All") else symbols[:50 if "50" in size else 100]
            with st.spinner(f"Requesting Yahoo closes for {len(target)} stocks..."):
                quotes, errors = _cached_yahoo(target)
            cache = {
                "quotes": quotes, "errors": errors,
                "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }
            st.session_state["fno_yahoo_snapshot"] = cache
        prices = {s: q for s, q in cache.get("quotes", {}).items() if s in symbols}
        st.caption("Yahoo bar prices · fetched " + cache.get("requested_at", "not yet"))
        errors = cache.get("errors", [])

    if errors:
        st.warning(
            f"{len(errors)} {provider} data requests failed; prices are not fabricated. "
            + "; ".join(errors[:2])
        )
    return provider, prices


def render_screener():
    st.subheader("Stock F&O Screener")
    st.caption(
        "NSE stock F&O lots load automatically, even without Dhan credentials. "
        "Dhan equity IDs are fetched only when prices are requested; Yahoo is backup. "
        "Contract value = stock price × selected-expiry lot size."
    )
    universe = _auto_universe()
    if not universe:
        return

    entries = [row.entry for row in universe]
    months = expiry_options(entries)
    expiry = st.selectbox(
        "F&O expiry", months, index=months.index(choose_expiry(entries)),
        key="fno_expiry",
    )
    available = [x for x in universe if expiry in x.entry.lots]
    source, prices = _fetch_prices("Dhan", available)

    a, b, c = st.columns(3)
    a.metric("F&O stocks", f"{len(available):,}")
    b.metric("Prices available", f"{len(prices):,}")
    c.metric("No price", f"{len(available)-len(prices):,}")
    if len(prices) != len(available):
        st.caption(
            "Not all stocks have quotes. Contract-value ranking covers the priced "
            "symbols only; unpriced stocks are listed last without invented values."
        )
    with st.container(border=True):
        x, y, z = st.columns([1.5, 1.2, 1.5])
        query = x.text_input("Find stock", placeholder="TCS / RELIANCE", key="fno_symbol_find")
        ordering = y.radio("Contract value order", ["Low → high", "High → low"],
                           horizontal=True, key="fno_value_sort")
        preset = z.selectbox(
            "Value filter",
            ["Custom", "Below ₹5 lakh", "Below ₹6 lakh", "Below ₹7 lakh",
             "Below ₹8 lakh", "Above ₹5 lakh", "Above ₹8 lakh"], key="fno_value_preset",
        )
        mincol, maxcol = st.columns(2)
        min_l = mincol.number_input("Minimum (₹ lakh)", min_value=0.0, value=0.0,
                                    step=0.5, key="fno_min_lakh")
        max_l = maxcol.number_input("Maximum (₹ lakh; 0 = no limit)", min_value=0.0,
                                    value=0.0, step=0.5, key="fno_max_lakh")
    caps = {
        "Below ₹5 lakh": (0, 5), "Below ₹6 lakh": (0, 6),
        "Below ₹7 lakh": (0, 7), "Below ₹8 lakh": (0, 8),
        "Above ₹5 lakh": (5, 0), "Above ₹8 lakh": (8, 0),
    }
    low, high = caps.get(preset, (min_l, max_l))
    try:
        rows = screen_rows(entries, expiry, prices, query, min_value=low * 100000,
                           max_value=high * 100000,
                           descending=ordering == "High → low")
    except ValueError as exc:
        st.error(str(exc))
        return
    st.markdown(f"**{len(rows):,} matching stocks**")
    if not rows:
        st.info("No stocks match. Try adjusting contract value or refreshing prices.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame, hide_index=True, use_container_width=True, height=560,
        column_config={
            "Symbol": st.column_config.TextColumn("Stock"),
            "Last price (₹)": st.column_config.NumberColumn("Stock price (₹)", format="₹%.2f"),
            "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
            "Contract value (₹)": st.column_config.NumberColumn("Contract value (₹)", format="₹%.2f"),
            "Quote time": st.column_config.TextColumn("Quote date/time"),
            "Price source": st.column_config.TextColumn("Source"),
        }
    )
    st.caption(
        f"Prices from **{source}** only. Contract value is indicative underlying "
        "notional—not option premium, required broker margin or a trading signal."
    )
    exported = io.StringIO()
    writer = csv.DictWriter(exported, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    st.download_button(
        "Export filtered CSV", data=exported.getvalue(),
        file_name=f"stock_fno_{expiry}_{source.lower().replace(' ', '_')}.csv",
        mime="text/csv", key="fno_export",
    )
