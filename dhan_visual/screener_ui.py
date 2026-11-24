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
from queue import SimpleQueue

import pandas as pd
import streamlit as st

from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.fast_universe import load_fast_catalogue, _write_cache
from dhan_visual.background_quotes import load_dhan_all, load_yahoo_all, now_utc
from dhan_visual.local_client_settings import read_client_id, save_client_id, valid_client_id
from dhan_visual.screener import choose_expiry, expiry_options, screen_rows


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


@st.cache_resource
def _market_pool():
    """Shared executor, but every quote snapshot is isolated to a browser session."""
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="fno-public-quotes")


def _dhan_credentials():
    """Remember only the Client ID locally; token remains in RAM or environment."""
    saved = read_client_id()
    default_id = os.environ.get("DHAN_CLIENT_ID", "") or saved
    configured = bool(default_id and os.environ.get("DHAN_ACCESS_TOKEN"))
    with st.expander("Dhan market-data login · read-only", expanded=not configured):
        st.caption(
            "Client ID is remembered once on this laptop. Dhan access tokens "
            "expire within 24 hours; paste your *current* token locally when "
            "needed. This studio does not automatically renew tokens or save "
            "them in its settings file. Never share an access token in chat."
        )
        cid = st.text_input(
            "Dhan Client ID", value=default_id,
            key="dhan_local_cid",
            help="Saved automatically to this PC's gitignored .dhan_cache/client_settings.json.",
        )
        current_id = cid.strip()
        if valid_client_id(current_id):
            if saved != current_id and not save_client_id(current_id):
                st.warning(
                    "Could not remember the Client ID on disk. "
                    "Check write access to the DhanPayoffStudio folder."
                )
            else:
                st.caption("Client ID remembered on this PC; no need to re-enter it tomorrow.")
        elif current_id:
            st.warning("Enter a valid numeric Dhan Client ID to remember it.")
        token = st.text_input(
            "Dhan access token · current 24-hour token",
            value=os.environ.get("DHAN_ACCESS_TOKEN", ""),
            type="password", key="dhan_local_access_token",
            help="For security, the access token is not saved to the local settings file.",
        )
    return cid.strip(), token.strip()


def _universe_key(universe):
    # The same cash stock is requested exactly once, regardless of expiry.
    symbols = ",".join(sorted({row.entry.symbol for row in universe}))
    return sha256(symbols.encode()).hexdigest()


def _start_dhan_prices(universe, cid, token, force=False):
    """Start all-stock market-data prefetch on first valid login, without click."""
    identity = sha256((cid + ":" + token + ":" + _universe_key(universe)).encode()).hexdigest()
    existing = st.session_state.get("fno_dhan_job")
    if existing and not existing["future"].done():
        if existing["key"] == identity:
            return identity
        # Keep old worker out of the new credentials' display path; it will
        # finish naturally without exposing results to the new login.
    if not force and st.session_state.get("fno_dhan_attempt_key") == identity:
        return identity
    st.session_state["fno_dhan_attempt_key"] = identity
    st.session_state["fno_dhan_job"] = {
        "key": identity,
        "future": _market_pool().submit(load_dhan_all, list(universe), cid, token),
        "started": now_utc(),
    }
    return identity


def _start_yahoo_prices(universe, expiry, force=False):
    """Queue all Yahoo stocks in 25-symbol background batches, automatically."""
    key = _universe_key(universe)
    current = st.session_state.get("fno_yahoo_job")
    if current and not current["future"].done() and current["key"] == key:
        return key
    if not force and st.session_state.get("fno_yahoo_attempt_key") == key:
        return key
    st.session_state["fno_yahoo_attempt_key"] = key
    old = st.session_state.get("fno_yahoo_snapshot") or {}
    # Prior prices *only* determine priority when their price × lot is known.
    # Unknown stocks remain eligible and every stock is requested.
    earlier = old.get("quotes", {})
    queue = SimpleQueue()
    if force:
        st.session_state.pop("fno_yahoo_snapshot", None)
    st.session_state["fno_yahoo_job"] = {
        "key": key, "queue": queue,
        "future": _market_pool().submit(
            load_yahoo_all, list(universe), queue, earlier, expiry, 25
        ),
        "started": now_utc(),
        "processed": 0,
        "total": len(universe),
    }
    return key


@st.fragment(run_every="2s")
def _poll_market_data():
    """Poll completed background work and progressively render partial Yahoo rows.

    Workers never invoke Streamlit; the main Streamlit thread is the only
    writer to session state or visible widgets.
    """
    updated = False
    dhan_job = st.session_state.get("fno_dhan_job")
    if dhan_job and dhan_job["future"].done():
        key = dhan_job["key"]
        try:
            response = dhan_job["future"].result()
            if st.session_state.get("fno_dhan_attempt_key") == key:
                st.session_state["fno_dhan_snapshot"] = {
                    "key": key, "quotes": dict(response.quotes),
                    "ids": dict(response.ids), "errors": list(response.errors),
                    "requested": response.requested,
                    "requested_at": response.requested_at,
                }
                ids = {sym: sec for sym, sec in response.ids.items() if sec > 0}
                if ids:
                    previous = st.session_state.get("fno_universe_data")
                    if previous:
                        data, provenance = previous
                        refreshed = [
                            DhanUnderlying(row.entry, ids.get(row.entry.symbol, row.equity_security_id))
                            for row in data
                        ]
                        # Public security IDs and lot sizes; never store token or prices on disk.
                        _write_cache(refreshed, "NSE lots + Dhan cash-equity IDs")
                        st.session_state["fno_universe_data"] = (
                            refreshed, provenance + " · equity IDs verified"
                        )
                updated = True
        except Exception as exc:
            if st.session_state.get("fno_dhan_attempt_key") == key:
                # Never echo raw token or raw HTTP body in exception details.
                st.session_state["fno_dhan_snapshot"] = {
                    "key": key, "quotes": {}, "ids": {},
                    "errors": [f"Background Dhan quote process: {type(exc).__name__}"],
                    "requested_at": now_utc(),
                }
                updated = True
        st.session_state.pop("fno_dhan_job", None)

    yahoo_job = st.session_state.get("fno_yahoo_job")
    if yahoo_job:
        queue = yahoo_job["queue"]
        old = st.session_state.get("fno_yahoo_snapshot") or {}
        if old.get("key") != yahoo_job["key"]:
            old = {"key": yahoo_job["key"], "quotes": {}, "errors": [],
                   "processed": 0, "total": yahoo_job["total"]}
        while not queue.empty():
            chunk = queue.get_nowait()
            combined = dict(old["quotes"])
            combined.update(chunk.quotes)
            old["quotes"] = combined
            old["errors"].extend(chunk.errors)
            old["processed"] = chunk.completed
            old["total"] = chunk.total
            old["requested_at"] = now_utc()
            updated = True
        if updated and st.session_state.get("fno_yahoo_attempt_key") == yahoo_job["key"]:
            st.session_state["fno_yahoo_snapshot"] = old
        if yahoo_job["future"].done():
            try:
                yahoo_job["future"].result()
            except Exception as exc:
                old["errors"].append(
                    f"Yahoo background process failed ({type(exc).__name__})"
                )
            old["complete"] = True
            st.session_state["fno_yahoo_snapshot"] = old
            st.session_state.pop("fno_yahoo_job", None)
            updated = True

    if updated:
        st.rerun()

    dhan_busy = st.session_state.get("fno_dhan_job")
    yahoo_busy = st.session_state.get("fno_yahoo_job")
    if dhan_busy:
        st.caption("Fetching all F&O cash-stock prices from Dhan in the background…")
    if yahoo_busy:
        snap = st.session_state.get("fno_yahoo_snapshot") or {}
        st.caption(
            f"Yahoo backup: scanned {snap.get('processed', 0)}/{yahoo_busy['total']} "
            "F&O stocks in automatic batches…"
        )


def _price_panel(universe, expiry, available):
    st.markdown("#### Prices · automatic background loading")
    left, right = st.columns([2, 1])
    with left:
        provider = st.radio(
            "Quote source", ["Dhan", "Yahoo Finance"], index=0,
            horizontal=True, key="fno_price_provider",
            help="Dhan is default. Yahoo is a selectable fallback; its historical "
                 "bar closes can be delayed or missing.",
        )
    with right:
        # Optional explicit force refresh is never required for first load.
        manual = st.button("Refresh now", key="fno_manual_price_refresh")

    all_symbols = {row.entry.symbol for row in universe}
    if provider == "Dhan":
        cid, token = _dhan_credentials()
        if cid and len(token) >= 16:
            identity = _start_dhan_prices(universe, cid, token, force=manual)
            snapshot = st.session_state.get("fno_dhan_snapshot") or {}
            if snapshot.get("key") != identity:
                snapshot = {}
            prices = {
                sym: value for sym, value in snapshot.get("quotes", {}).items()
                if sym in all_symbols
            }
            if st.session_state.get("fno_dhan_job"):
                st.caption(
                    f"**Dhan:** fetching prices automatically for all "
                    f"{len(universe)} stock underlyings. No further click needed."
                )
            elif snapshot.get("requested_at"):
                st.caption(
                    "Dhan bulk NSE_EQ snapshot · requested "
                    + snapshot["requested_at"]
                    + " · not an exchange last-trade timestamp"
                )
            for error in (snapshot.get("errors", []) or [])[:3]:
                st.warning(error)
        else:
            prices = {}
            st.info(
                "The F&O list is automatic. To fetch **Dhan** prices automatically, "
                "enter your Client ID and current access token once in the local "
                "login panel. Your active subscription alone does not authenticate "
                "this Streamlit app. You may select Yahoo without Dhan login."
            )
    else:
        key = _start_yahoo_prices(universe, expiry, force=manual)
        snap = st.session_state.get("fno_yahoo_snapshot") or {}
        if snap.get("key") != key:
            snap = {}
        prices = {
            sym: value for sym, value in snap.get("quotes", {}).items()
            if sym in all_symbols
        }
        total = len(universe)
        scanned = snap.get("processed", 0)
        st.caption(
            f"Yahoo backup: {scanned}/{total} symbols processed; "
            f"{len(prices)} prices found. Indicative delayed historical bars, "
            "not broker LTP."
        )
        for error in (snap.get("errors") or [])[:2]:
            st.warning(error)

    if st.session_state.get("fno_dhan_job") or st.session_state.get("fno_yahoo_job"):
        _poll_market_data()
    return provider, prices


def render_screener():
    st.subheader("NSE Stock F&O · Contract Value Screener")
    st.caption(
        "F&O lot sizes load automatically. Dhan starts a full-universe market-data "
        "snapshot in the background after local authentication; Yahoo backup "
        "progressively loads every stock. Contract value = cash price × lot size."
    )
    universe = _auto_universe()
    if not universe:
        return
    entries = [row.entry for row in universe]
    expiries = expiry_options(entries)
    expiry = st.selectbox(
        "F&O expiry month", expiries, index=expiries.index(choose_expiry(entries)),
        key="fno_expiry",
    )
    available = [row for row in universe if expiry in row.entry.lots]
    provider, all_prices = _price_panel(universe, expiry, available)
    prices = {sym: quote for sym, quote in all_prices.items()
              if sym in {row.entry.symbol for row in available}}
    filled = len(prices)
    all_count = len(universe)
    cols = st.columns(4)
    cols[0].metric("Listed F&O stocks", f"{all_count:,}")
    cols[1].metric("For selected expiry", f"{len(available):,}")
    cols[2].metric("Prices available", f"{filled:,}/{len(available):,}")
    cols[3].metric("Still unpriced", f"{len(available) - filled:,}")

    if filled != len(available):
        st.caption(
            "A complete contract-value ranking requires quotes for every stock. "
            "Until then, sorting and amount filters include priced stocks only. "
            "Unquoted rows are N/A, never treated as ₹0."
        )

    st.markdown("#### Filter & sort")
    with st.container(border=True):
        first, second, third = st.columns([1.4, 1.1, 1.3])
        search = first.text_input(
            "Stock / symbol", placeholder="TCS, SBIN, RELIANCE",
            key="fno_symbol_find",
        )
        sort = second.radio(
            "Sort by contract value", ["Low → high", "High → low"],
            key="fno_value_sort", horizontal=True,
        )
        choices = [
            "All stocks", "Below ₹5 lakh", "Below ₹6 lakh",
            "Below ₹7 lakh", "Below ₹8 lakh",
            "Above ₹5 lakh", "Above ₹8 lakh", "Custom",
        ]
        preset = third.selectbox(
            "Show contract sizes", choices, index=3,
            key="fno_value_preset",
            help="Starts with <₹7 lakh. Change to All stocks to see the full F&O universe.",
        )
        a, b = st.columns(2)
        min_l = a.number_input(
            "Minimum (₹ lakh)", min_value=0., value=0., step=.5,
            key="fno_min_lakh", disabled=preset != "Custom",
        )
        max_l = b.number_input(
            "Maximum (₹ lakh, 0 means no upper limit)",
            min_value=0., value=0., step=.5, key="fno_max_lakh",
            disabled=preset != "Custom",
        )

    caps = {
        "All stocks": (0, 0),
        "Below ₹5 lakh": (0, 5), "Below ₹6 lakh": (0, 6),
        "Below ₹7 lakh": (0, 7), "Below ₹8 lakh": (0, 8),
        "Above ₹5 lakh": (5, 0), "Above ₹8 lakh": (8, 0),
    }
    low, high = caps.get(preset, (min_l, max_l))
    # During the first quote load the screener still shows the full symbol/lot
    # list; otherwise the default <₹7L filter would misleadingly show zero.
    no_prices_yet = not prices
    effective_low, effective_high = (0, 0) if no_prices_yet else (low, high)
    try:
        rows = screen_rows(
            entries, expiry, prices, search,
            min_value=effective_low * 100000,
            max_value=effective_high * 100000,
            descending=sort == "High → low",
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    if no_prices_yet and preset != "All stocks":
        st.info(
            f"Showing available symbols and lot sizes while prices load. "
            f"The **{preset}** filter will apply automatically when quotes arrive."
        )

    st.markdown(f"**{len(rows):,} displayed stocks** · {filled:,} with {provider} prices")
    if not rows:
        st.info("No stocks match the selected value filter. The source may also have missing prices.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame, use_container_width=True, hide_index=True, height=580,
        column_config={
            "Symbol": st.column_config.TextColumn("Stock"),
            "Company": st.column_config.TextColumn("Company"),
            "Last price (₹)": st.column_config.NumberColumn(
                "Cash price (₹)", format="₹%.2f"),
            "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
            "Contract value (₹)": st.column_config.NumberColumn(
                "Price × Lot (₹)", format="₹%.2f"),
            "Quote time": st.column_config.TextColumn("Quote time"),
            "Price source": st.column_config.TextColumn("Source"),
        },
    )
    st.caption(
        f"**Data source:** {provider}. Contract value is the underlying notional "
        "and is NOT option margin, broker execution cost or an investment recommendation."
    )
    exported = io.StringIO()
    writer = csv.DictWriter(exported, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    st.download_button(
        "Download filtered CSV", data=exported.getvalue(),
        file_name=f"fno_stock_values_{expiry}_{provider.lower().replace(' ', '_')}.csv",
        mime="text/csv", key="fno_export",
    )
