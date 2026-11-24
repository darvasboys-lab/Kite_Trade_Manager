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
from dhan_visual.auto_auth import (
    AutoAuthError, AutoAccess, is_configured, store_credentials, forget_credentials,
    read_cached_token, load_or_generate,
)
import time
from dhan_visual.screener import choose_expiry, expiry_options, screen_rows
from dhan_visual.option_panel import render_atm_panel
from dhan_visual.straddle_panel import render_straddle_panel, available_otm_calls
from dhan_visual.technical_ui import technical_values_for_table, technical_visible_stage, attach_technical_columns
from dhan_visual.table_layout import (
    autofit_column_config, autofit_enabled, click_autofit, dataframe_key,
)


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
        # No large list-status row above the compact screener toolbar.
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


def _auto_enable(cid):
    """Save the setup key/PIN into Windows Credential Manager, not source files."""
    try:
        if not st.session_state.get("_dhan_auto_ack"):
            raise AutoAuthError(
                "Confirm that creating a new Dhan token may affect ResultBot."
            )
        store_credentials(cid, st.session_state.get("_dhan_setup_pin", ""),
                          st.session_state.get("_dhan_setup_seed", ""))
        # A manually entered (possibly expired) token must not silently block
        # the freshly enabled automatic login. This callback runs before inputs.
        st.session_state["dhan_local_access_token"] = ""
        st.session_state["dhan_auto_message"] = "Secure TOTP auto-login enabled on this laptop."
        st.session_state.pop("dhan_auto_error", None)
        st.session_state.pop("dhan_auto_auth_fail_at", None)
        st.session_state.pop("dhan_auto_access", None)
    except AutoAuthError as exc:
        st.session_state["dhan_auto_error"] = str(exc)
    finally:
        # Button callbacks execute before the widgets are re-created on rerun.
        st.session_state["_dhan_setup_pin"] = ""
        st.session_state["_dhan_setup_seed"] = ""
        st.session_state["_dhan_auto_ack"] = False


def _auto_disable(cid):
    try:
        forget_credentials(cid)
        st.session_state.pop("dhan_auto_access", None)
        st.session_state.pop("dhan_auto_error", None)
        st.session_state.pop("dhan_auto_auth_fail_at", None)
        st.session_state["dhan_auto_message"] = (
            "Auto-login disabled. PIN, TOTP and cached token deleted locally. "
            "Issued tokens must be revoked separately in Dhan Web."
        )
    except AutoAuthError as exc:
        st.session_state["dhan_auto_error"] = str(exc)


def _start_auto_login(cid, force=False):
    job = st.session_state.get("dhan_auto_auth_job")
    if job and not job["future"].done():
        return
    if not force and (
        time.monotonic() - st.session_state.get("dhan_auto_auth_fail_at", -1e12)
        < 600
    ):
        return
    if not force:
        ready = read_cached_token(cid)
        if ready:
            st.session_state["dhan_auto_access"] = ready
            return
    st.session_state.pop("dhan_auto_error", None)
    st.session_state["dhan_auto_auth_job"] = {
        "cid": cid,
        "future": _market_pool().submit(load_or_generate, cid),
    }


@st.fragment(run_every="15s")
def _auto_login_heartbeat(cid):
    """While app runs: process authentication and renew close to expiry."""
    if not is_configured(cid):
        return
    job = st.session_state.get("dhan_auto_auth_job")
    if job:
        if job["future"].done():
            try:
                access = job["future"].result()
                if job["cid"] == cid and is_configured(cid):
                    st.session_state["dhan_auto_access"] = access
                    st.session_state.pop("dhan_auto_auth_fail_at", None)
                    st.session_state.pop("dhan_auto_error", None)
            except AutoAuthError as exc:
                st.session_state["dhan_auto_auth_fail_at"] = time.monotonic()
                st.session_state["dhan_auto_error"] = str(exc)
            except Exception:
                st.session_state["dhan_auto_auth_fail_at"] = time.monotonic()
                st.session_state["dhan_auto_error"] = "Dhan TOTP login failed; retry securely"
            st.session_state.pop("dhan_auto_auth_job", None)
            st.rerun(scope="app")
        st.caption("Secure Dhan TOTP login is running in the background…")
    else:
        current = st.session_state.get("dhan_auto_access")
        if not isinstance(current, AutoAccess) or not current.usable():
            _start_auto_login(cid)


def _auto_login_panel(cid, manual_token):
    """Optional local TOTP setup, independent of ResultBot's secrets."""
    if not valid_client_id(cid):
        return ""
    enabled = is_configured(cid)
    with st.expander("Automatic Dhan access token · TOTP setup", expanded=False):
        st.caption(
            "Provide your Dhan 6-digit PIN and TOTP setup secret ONCE. "
            "Windows Credential Manager protects these locally. "
            "Dhan returns a 24-hour token; while this dashboard is running "
            "it can renew automatically without pasting a daily token."
        )
        st.warning(
            "This Dhan account may also be used by ResultBot. "
            "Creating a separate token can sometimes interrupt other sessions. "
            "Enable only if you accept this risk. ResultBot itself will not be changed."
        )
        if enabled:
            st.success("Secure TOTP settings are saved in Windows Credential Manager.")
            st.button("Delete local TOTP/PIN and disable auto-login",
                      key="dhan_auto_delete", on_click=_auto_disable, args=(cid,))
        else:
            st.text_input("6-digit Dhan PIN", key="_dhan_setup_pin",
                          type="password", max_chars=6, value="")
            st.text_input("16-character TOTP setup secret (NOT the 6-digit code)",
                          key="_dhan_setup_seed", type="password", value="")
            st.checkbox(
                "I understand that creating a new Dhan token may affect ResultBot",
                key="_dhan_auto_ack", value=False,
            )
            st.button("Save securely and enable automatic token generation",
                      key="dhan_auto_enable", on_click=_auto_enable, args=(cid,))
        if st.session_state.get("dhan_auto_message"):
            st.info(st.session_state["dhan_auto_message"])
        if st.session_state.get("dhan_auto_error"):
            st.error(st.session_state["dhan_auto_error"])

    if manual_token or not enabled:
        return ""
    stored = st.session_state.get("dhan_auto_access")
    if not isinstance(stored, AutoAccess) or not stored.usable():
        stored = read_cached_token(cid)
        if stored:
            st.session_state["dhan_auto_access"] = stored
        else:
            _start_auto_login(cid)
    _auto_login_heartbeat(cid)
    if st.session_state.get("dhan_auto_error"):
        st.error(st.session_state["dhan_auto_error"])
        if st.button("Retry Dhan TOTP login", key="dhan_auto_retry"):
            _start_auto_login(cid, force=True)
    if stored and stored.usable():
        st.caption(
            "Automatic Dhan token active until "
            + stored.expires.astimezone().strftime("%d %b %Y %H:%M")
        )
        return stored.token
    return ""


def _dhan_credentials():
    saved = read_client_id()
    default_id = os.environ.get("DHAN_CLIENT_ID", "") or saved
    configured = bool(default_id and (os.environ.get("DHAN_ACCESS_TOKEN") or is_configured(default_id)))
    with st.expander("Dhan market-data login · read-only", expanded=not configured):
        st.caption(
            "Client ID is remembered locally. Choose manual token entry or "
            "enable secure TOTP auto-login below. Never paste credentials in chat."
        )
        cid = st.text_input(
            "Dhan Client ID", value=default_id,
            key="dhan_local_cid",
            help="Saved locally to the gitignored .dhan_cache/client_settings.json",
        )
        current_id = cid.strip()
        if valid_client_id(current_id):
            if saved != current_id and not save_client_id(current_id):
                st.warning("Could not save Client ID locally")
            else:
                st.caption("Client ID remembered on this PC.")
        elif current_id:
            st.warning("Enter a numeric Dhan Client ID")
        manual = st.text_input(
            "Dhan access token · manual option",
            value=os.environ.get("DHAN_ACCESS_TOKEN", ""),
            type="password", key="dhan_local_access_token",
            help="Optional if TOTP auto-login is enabled.",
        )
    automatic = _auto_login_panel(current_id, manual.strip())
    return current_id, (manual.strip() or automatic)


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
        st.caption("Dhan · updating prices…")
    if yahoo_busy:
        snap = st.session_state.get("fno_yahoo_snapshot") or {}
        st.caption(f"Yahoo · {snap.get('processed', 0)}/{yahoo_busy['total']} scanned")


def _price_panel(universe, expiry, available, provider, manual):
    """Quote-source controls live in the compact top-right toolbar."""
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
                st.caption(f"Dhan · loading {len(universe)} stocks")
            elif snapshot.get("requested_at"):
                st.caption(f"Dhan · {len(prices)} prices ready")
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
        st.caption(f"Yahoo · {len(prices)}/{total} priced · {scanned}/{total} scanned")
        for error in (snap.get("errors") or [])[:2]:
            st.warning(error)

    if st.session_state.get("fno_dhan_job") or st.session_state.get("fno_yahoo_job"):
        _poll_market_data()
    return provider, prices


def render_screener():
    universe = _auto_universe()
    if not universe:
        return
    entries = [row.entry for row in universe]
    expiries = expiry_options(entries)

    st.markdown(
        """<div class="ccs-screen-head">
             <div><span class="ccs-screen-kicker">NSE STOCK F&O · READ ONLY</span>
             <h3>Market screener <span class="ccs-accent">/</span> Strategy signals</h3>
             <p>Contract value · EMA trend · monthly R1 · options premium</p></div>
           </div>""", unsafe_allow_html=True,
    )

    # Compact one-row toolbar: visible expiry + three tucked-away controls.
    # All prior Streamlit widget keys are preserved for saved user selections.
    expiry_col, search_col, filters_col, source_col, autofit_col = st.columns(
        [1.05, 0.9, 1.2, 0.85, 1.05], gap="small",
    )
    with expiry_col:
        expiry = st.selectbox(
            "F&O expiry month", expiries,
            index=expiries.index(choose_expiry(entries)),
            key="fno_expiry", label_visibility="collapsed",
        )
    with search_col:
        with st.popover("⌕ Search", use_container_width=True):
            search = st.text_input(
                "Find stock / symbol", placeholder="TCS, SBIN, RELIANCE",
                key="fno_symbol_find",
            )
    with filters_col:
        with st.popover("⚙ Filter & sort", use_container_width=True):
            sort = st.radio(
                "Sort contract value", ["Low → high", "High → low"],
                key="fno_value_sort", horizontal=True,
            )
            choices = [
                "All stocks", "Below ₹5 lakh", "Below ₹6 lakh",
                "Below ₹7 lakh", "Below ₹8 lakh",
                "Above ₹5 lakh", "Above ₹8 lakh", "Custom",
            ]
            preset = st.selectbox(
                "Contract value range", choices, index=3,
                key="fno_value_preset",
            )
            min_l = st.number_input(
                "Minimum (₹ lakh)", min_value=0., value=0., step=.5,
                key="fno_min_lakh", disabled=preset != "Custom",
            )
            max_l = st.number_input(
                "Maximum (₹ lakh; 0 = no upper limit)",
                min_value=0., value=0., step=.5,
                key="fno_max_lakh", disabled=preset != "Custom",
            )
            compact = st.toggle(
                "Show values in ₹L", value=True,
                key="fno_compact_contract_value",
                help="₹270,500 → ₹2.7L; raw numeric values remain unchanged.",
            )
    with source_col:
        with st.popover("◉ Source", use_container_width=True):
            provider = st.radio(
                "Quote source", ["Dhan", "Yahoo Finance"], index=1,
                key="fno_price_provider", horizontal=True,
                help="Yahoo is default; Dhan needs a valid local data token.",
            )
            manual = st.button("↻ Refresh prices", key="fno_manual_price_refresh")
            if st.button("↻ Refresh stock list", key="fno_refresh_universe"):
                _start_universe(force=True)
                st.rerun()
            st.caption("Yahoo bars may be delayed.")
    with autofit_col:
        st.button(
            "↔ Auto-fit all", key="ccs_autofit_all_button",
            on_click=click_autofit, args=(st.session_state,),
            use_container_width=True,
            help="Fit all visible columns to their contents across the "
                 "stock screener, ATM rankings and strategy tables. "
                 "Click again to reset manually adjusted widths.",
        )

    available = [row for row in universe if expiry in row.entry.lots]
    provider, all_prices = _price_panel(universe, expiry, available, provider, manual)
    prices = {
        sym: quote for sym, quote in all_prices.items()
        if sym in {row.entry.symbol for row in available}
    }
    filled = len(prices)
    st.markdown(
        f'<div class="ccs-market-strip">'
        f'<span><b>{len(available)}</b> F&O stocks</span>'
        f'<span class="ccs-strip-dot">·</span>'
        f'<span><b>{filled}</b> quotes</span>'
        f'<span class="ccs-strip-dot">·</span>'
        f'<span>{provider}</span>'
        f'<span class="ccs-strip-dot">·</span>'
        f'<span>{preset} · {sort}</span>'
        f'</div>', unsafe_allow_html=True,
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
        st.caption(f"Loading quotes · **{preset}** will apply when prices arrive.")

    # Technical download is separate from cash quotes and covers the full F&O
    # universe, not just the ₹7 lakh/selected-symbol table subset.
    technical_symbols = [item.entry.symbol for item in universe]
    technicals = technical_values_for_table(technical_symbols)
    visible_stage = technical_visible_stage(technical_symbols)
    st.markdown(f"**{len(rows):,} displayed stocks** · {filled:,} with {provider} prices")
    if not rows:
        st.info("No stocks match the selected value filter. The source may also have missing prices.")
    else:
        frame = pd.DataFrame(rows)
        config = {
            "Symbol": st.column_config.TextColumn("Stock"),
            "Last price (₹)": st.column_config.NumberColumn(
                "Cash price (₹)", format="₹%.2f"),
            "Lot size": st.column_config.NumberColumn("Lot size", format="%d"),
        }
        if compact:
            # Keep numeric values and sorting; only the table display changes.
            frame["Contract value (₹L)"] = frame["Contract value (₹)"] / 100000
            frame = frame.drop(columns=["Contract value (₹)"])
            frame = frame[[
                "Symbol", "Last price (₹)", "Lot size",
                "Contract value (₹L)",
            ]]
            config["Contract value (₹L)"] = st.column_config.NumberColumn(
                "Price × Lot (₹L)", format="₹%.1fL"
            )
        else:
            config["Contract value (₹)"] = st.column_config.NumberColumn(
                "Price × Lot (₹)", format="₹%.0f"
            )
        # Keep source/quote time in CSV and expandable notes, not the wide table.
        # Company remains available for existing name-search matching,
        # but is removed from the DISPLAY and the filtered CSV export.
        frame = frame.drop(
            columns=["Company", "Quote time", "Price source"], errors="ignore"
        )
        frame = attach_technical_columns(
            frame, technicals, stage=visible_stage,
        )
        otm_calls = available_otm_calls(available, expiry)
        # Single numeric column. A missing option-chain snapshot remains N/A.
        frame["5%+ CE premium (%)"] = [
            float(otm_calls[symbol].premium_pct) if symbol in otm_calls else None
            for symbol in frame["Symbol"]
        ]
        config["5%+ CE premium (%)"] = st.column_config.NumberColumn(
            "CE ≥+5% (%)", format="%.2f%%", width="medium",
            help="First real listed CE strike >= Dhan chain CMP × 1.05. "
                 "CE LTP / Dhan CMP ×100. Populates from the SAME background "
                 "ATM-straddle scan; no extra Dhan requests. Missing = N/A.",
        )
        for title, note in (
            ("EMA 200", "Daily 200-session EMA"),
            ("EMA 50", "Daily 50-session EMA"),
            ("EMA 20", "Daily 20-session EMA"),
            ("EMA 10", "Daily 10-session EMA"),
            ("Monthly R1", "Current calendar month's standard R1, calculated from previous completed month's H/L/C"),
        ):
            config[title] = st.column_config.TextColumn(title, help=note, width="medium")
        st.dataframe(
            frame, use_container_width=True, hide_index=True, height=580,
            key=dataframe_key("ccs_stock_screener", st.session_state),
            column_config=autofit_column_config(
                config, autofit_enabled(st.session_state),
            ),
        )
    st.caption(
        f"{provider} · {filled}/{len(available)} prices · "
        "✅ CMP above level · no tick = at/below · — waiting/unavailable · "
        "Yahoo indicators reveal EMA200 → EMA50 → EMA20 → EMA10 → R1 · "
        "CE ≥5% from opt-in Dhan scan"
    )
    with st.expander("Source details and timestamps", expanded=False):
        st.caption(f"Instrument list: {(st.session_state.get('fno_universe_data') or (None, 'Unknown'))[1]}")
        if provider == "Yahoo Finance":
            details = st.session_state.get("fno_yahoo_snapshot") or {}
        else:
            details = st.session_state.get("fno_dhan_snapshot") or {}
        st.caption(f"Latest quote request: {details.get('requested_at', 'not yet')}")
        st.caption(
            "Quote-request time is not exchange last-trade time. Yahoo bar closes "
            "may be stale or delayed. Contract value is not option margin."
        )
    # The straddle panel scans the ENTIRE expiry universe, not the ₹7L-filtered table.
    render_straddle_panel(available, expiry)
    render_atm_panel(available, rows, expiry)
    if rows:
        # Export fully computed cached levels without waiting for visual
        # stage animation; no extra Yahoo requests are made.
        exported = attach_technical_columns(
            pd.DataFrame(rows).drop(columns=["Company"], errors="ignore"),
            technicals, stage=4,
        )
        otm_calls = available_otm_calls(available, expiry)
        exported["5%+ CE premium (%)"] = [
            float(otm_calls[sym].premium_pct) if sym in otm_calls else None
            for sym in exported["Symbol"]
        ]
        exported["5%+ CE strike (₹)"] = [
            float(otm_calls[sym].strike) if sym in otm_calls else None
            for sym in exported["Symbol"]
        ]
        exported["Dhan CMP for CE % (₹)"] = [
            float(otm_calls[sym].underlying_ltp) if sym in otm_calls else None
            for sym in exported["Symbol"]
        ]
        exported["Indicators as of"] = [
            technicals[sym].asof if sym in technicals else ""
            for sym in exported["Symbol"]
        ]
        st.download_button(
            "Download filtered CSV", data=exported.to_csv(index=False),
            file_name=f"fno_stock_values_{expiry}_{provider.lower().replace(' ', '_')}.csv",
            mime="text/csv", key="fno_export",
        )
