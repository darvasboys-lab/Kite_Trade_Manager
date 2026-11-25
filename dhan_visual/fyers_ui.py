"""Streamlit FYERS read-only onboarding and shared, bounded screener jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from queue import Empty, SimpleQueue
from threading import Event

import pandas as pd
import streamlit as st

from .fyers_readonly import (
    FyersAccess, FyersDataError, authorization_url, exchange_code,
    fetch_cash_quotes, fetch_chain, forget_vault, new_state,
    parse_authorization_return, read_access_from_vault, read_app_from_vault,
    resolve_expiry_timestamp, save_access_to_vault, save_app_to_vault,
    validate_app_id, _valid_secret,
)
from .ce_eod_remote import IST


@st.cache_resource
def _fyers_pool():
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="fyers-readonly")


def connected_fyers() -> FyersAccess | None:
    access = st.session_state.get("_fyers_access")
    if isinstance(access, FyersAccess) and access.valid():
        return access
    access = read_access_from_vault()
    if access is not None:
        st.session_state["_fyers_access"] = access
    else:
        st.session_state.pop("_fyers_access", None)
    return access


def render_fyers_settings():
    st.markdown("### FYERS · read-only market data")
    st.caption(
        "FYERS supplies market quotes and genuine CE/PE option chains. "
        "Dhan is a separate connection; FYERS cannot place orders from this app. "
        "A non-trading API app requires no static IP. Sign in with FYERS daily "
        "after its 6:30 AM IST access-token expiry."
    )
    st.markdown(
        "[Create a non-trading FYERS app]"
        "(https://fyers.in/web/api-dashboard/user-apps) · "
        "App name: **Covered Call Strategy Builder** · "
        "Redirect URL: `https://trade.fyers.in/api-login/redirect-uri/index.html`"
    )
    existing = read_app_from_vault()
    app_id = st.text_input(
        "FYERS App ID (ends in -100)",
        value=existing[0] if existing else "",
        key="fyers_local_app_id", max_chars=50,
    ).strip()
    entered_secret = st.text_input(
        "FYERS App Secret · blank uses your secure saved secret",
        value="", type="password", key="fyers_local_app_secret",
        help="Stored secrets are never re-sent to the browser. "
             "Type only once for first-time setup or replacement.",
    )
    app_secret = (entered_secret or
                  (existing[1] if existing and existing[0] == app_id else ""))
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Save in Windows Credential Manager", key="fyers_save_keys",
                     use_container_width=True):
            try:
                save_app_to_vault(app_id, app_secret)
                st.success("FYERS app settings saved securely on this Windows user account.")
            except FyersDataError as exc:
                st.warning(str(exc))
    with col2:
        if st.button("1 · Begin FYERS login", key="fyers_start_login",
                     use_container_width=True):
            try:
                validate_app_id(app_id)
                _valid_secret(app_secret)
                st.session_state["_fyers_pending_app"] = app_id
                st.session_state["_fyers_pending_secret"] = app_secret
                st.session_state["_fyers_oauth_state"] = new_state()
            except FyersDataError as exc:
                st.error(str(exc))
    pending_app = st.session_state.get("_fyers_pending_app")
    state = st.session_state.get("_fyers_oauth_state")
    if pending_app and state:
        st.link_button(
            "2 · Open secure FYERS login (new browser tab)",
            url=authorization_url(pending_app, state),
            use_container_width=True,
        )
        st.caption(
            "After signing in to FYERS, copy the FULL address from the "
            "FYERS redirect browser tab (including auth_code and state). "
            "Paste it below ON THIS PC, not in ChatGPT or GitHub."
        )
        redirected = st.text_input(
            "3 · Paste full FYERS redirect URL",
            type="password", key="fyers_redirect_return",
        )
        if st.button("4 · Connect FYERS market data", key="fyers_finish_login",
                     use_container_width=True):
            try:
                code = parse_authorization_return(redirected, state)
                access = exchange_code(
                    pending_app, st.session_state["_fyers_pending_secret"], code,
                )
                st.session_state["_fyers_access"] = access
                st.session_state["fno_fyers_select_on_next_run"] = True
                try:
                    save_access_to_vault(access)
                    st.session_state["_fyers_session_note"] = (
                        "Connected and token safely stored in Windows Credential Manager."
                    )
                except FyersDataError:
                    st.session_state["_fyers_session_note"] = (
                        "Connected for this browser session only. Secure storage unavailable."
                    )
                for key in (
                    "_fyers_pending_app", "_fyers_pending_secret",
                    "_fyers_oauth_state", "fyers_redirect_return",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
            except FyersDataError as exc:
                st.error(str(exc))
    access = connected_fyers()
    if access:
        st.success(
            "✓ FYERS data-only session connected · token expires "
            f"{access.expires_at.astimezone(IST).strftime('%d %b %Y %I:%M %p')} IST"
        )
        if st.session_state.get("_fyers_session_note"):
            st.caption(st.session_state["_fyers_session_note"])
        if st.button("Test FYERS TCS quote (read-only)", key="fyers_verify_quote"):
            try:
                quotes = fetch_cash_quotes(access, ["TCS"])
                value = quotes.get("TCS")
                if value:
                    st.success(f"FYERS TCS stock quote available · ₹{value.price} · {value.timestamp}")
                else:
                    st.warning("FYERS authorized; TCS stock quote not returned.")
            except FyersDataError as exc:
                st.warning(str(exc))
        if st.button("Disconnect and forget FYERS credentials on this PC",
                     key="fyers_remove_connection",
                     help="Stops local FYERS scanning and forgets the saved "
                          "credentials. Existing broker-issued tokens are not revoked."):
            chain = st.session_state.get("fno_fyers_chain_job") or {}
            if chain.get("cancel"):
                chain["cancel"].set()
            technical_job = st.session_state.get("fno_technical_job") or {}
            if st.session_state.get("fno_price_provider") == "FYERS" and technical_job.get("stop"):
                technical_job["stop"].set()
            for key in ("fno_fyers_cash_snapshot", "fno_fyers_chain_snapshot"):
                st.session_state.pop(key, None)
            forget_vault()
            st.session_state.pop("_fyers_access", None)
            st.session_state.pop("_fyers_session_note", None)
            st.session_state.pop("fyers_local_app_id", None)
            st.session_state.pop("fyers_local_app_secret", None)
            st.session_state["fno_fyers_disconnect_next_run"] = True
            st.rerun()
    else:
        st.caption("FYERS not connected yet. NSE EOD remains available as the fallback.")


def _scope(access: FyersAccess, symbols: list[str], expiry: str = "") -> str:
    value = "|".join(sorted(set(symbols)))
    return sha256((access.app_id + ":" + access.token + ":" + expiry
                   + ":" + value).encode()).hexdigest()


def _start_fyers_quotes(access: FyersAccess, symbols: list[str], *, force=False) -> str:
    key = _scope(access, symbols)
    job = st.session_state.get("fno_fyers_cash_job")
    if job and not job["future"].done():
        return key
    cached = st.session_state.get("fno_fyers_cash_snapshot") or {}
    if (not force and cached.get("key") == key and
            (datetime.now(timezone.utc) - cached["at"]).total_seconds() < 300):
        return key
    st.session_state["fno_fyers_cash_job"] = {
        "key": key,
        "future": _fyers_pool().submit(fetch_cash_quotes, access, list(symbols)),
    }
    return key


@st.fragment(run_every="3s")
def _fyers_quotes_heartbeat():
    job = st.session_state.get("fno_fyers_cash_job")
    if not job or not job["future"].done():
        return
    try:
        quotes = job["future"].result()
        st.session_state["fno_fyers_cash_snapshot"] = {
            "key": job["key"], "quotes": quotes,
            "at": datetime.now(timezone.utc),
            "error": "",
        }
    except FyersDataError as exc:
        st.session_state["fno_fyers_cash_snapshot"] = {
            "key": job["key"], "quotes": {},
            "at": datetime.now(timezone.utc),
            "error": str(exc),
        }
    except Exception:
        st.session_state["fno_fyers_cash_snapshot"] = {
            "key": job["key"], "quotes": {},
            "at": datetime.now(timezone.utc),
            "error": "Unexpected FYERS cash-data error.",
        }
    st.session_state.pop("fno_fyers_cash_job", None)
    st.rerun(scope="app")


def fyers_cash_prices(symbols: list[str], *, force=False) -> dict:
    access = connected_fyers()
    if not access:
        return {}
    key = _start_fyers_quotes(access, symbols, force=force)
    _fyers_quotes_heartbeat()
    snap = st.session_state.get("fno_fyers_cash_snapshot") or {}
    if snap.get("key") == key:
        if snap.get("error"):
            st.caption("FYERS stock quotes: " + snap["error"])
        return snap.get("quotes") or {}
    if st.session_state.get("fno_fyers_cash_job"):
        st.caption("FYERS cash quotes · loading automatically…")
    return {}


def _fyers_chain_worker(access: FyersAccess, symbols: list[str],
                        expiry: str, updates: SimpleQueue, cancel: Event):
    found, failed, count = {}, {}, 0
    try:
        expiry_epoch, expiry_day = resolve_expiry_timestamp(
            access, expiry, probe=symbols[0],
        )
        updates.put({"expiry_date": expiry_day})
    except FyersDataError as exc:
        updates.put({"fatal": str(exc), "complete": True})
        return
    for sym in symbols:
        if cancel.is_set():
            updates.put({"complete": True, "cancelled": True})
            return
        try:
            call, straddle = fetch_chain(access, sym, expiry, expiry_epoch)
            entry = {"symbol": sym, "call": call, "straddle": straddle}
            updates.put(entry)
        except FyersDataError as exc:
            count += 1
            if count <= 3:
                updates.put({"error": f"{sym}: {exc}"})
            if "limit" in str(exc).lower() or "unauthorized" in str(exc).lower() or "expired" in str(exc).lower():
                updates.put({"fatal": str(exc), "complete": True})
                return
        except Exception:
            count += 1
        finally:
            updates.put({"step": 1})
    updates.put({"complete": True})


def _start_fyers_chain(access: FyersAccess, symbols: list[str],
                       expiry: str, *, force=False) -> str:
    key = _scope(access, symbols, expiry)
    job = st.session_state.get("fno_fyers_chain_job")
    if job and not job["future"].done():
        if force and job["key"] != key:
            job["cancel"].set()
        return key
    previous = st.session_state.get("fno_fyers_chain_snapshot") or {}
    if previous.get("key") == key and not force:
        return key
    updates, cancel = SimpleQueue(), Event()
    future = _fyers_pool().submit(
        _fyers_chain_worker, access, list(symbols), expiry, updates, cancel,
    )
    st.session_state["fno_fyers_chain_job"] = {
        "key": key, "future": future, "updates": updates, "cancel": cancel,
    }
    st.session_state["fno_fyers_chain_snapshot"] = {
        "key": key, "calls": {}, "straddles": {}, "total": len(symbols),
        "processed": 0, "errors": [], "complete": False, "expiry_date": "",
    }
    return key


@st.fragment(run_every="3s")
def _fyers_chain_heartbeat():
    job = st.session_state.get("fno_fyers_chain_job")
    if not job:
        return
    snap = st.session_state.get("fno_fyers_chain_snapshot") or {}
    if job["key"] != snap.get("key"):
        return
    updated = False
    queue = job["updates"]
    while not queue.empty():
        event = queue.get_nowait()
        updated = True
        if "step" in event:
            snap["processed"] += event["step"]
        if "symbol" in event:
            if event["call"] is not None:
                snap["calls"][event["symbol"]] = event["call"]
            if event["straddle"] is not None:
                snap["straddles"][event["symbol"]] = event["straddle"]
        if "error" in event and len(snap["errors"]) < 3:
            snap["errors"].append(event["error"])
        if "fatal" in event:
            snap["errors"].append(event["fatal"])
        if "expiry_date" in event:
            snap["expiry_date"] = event["expiry_date"]
        if "complete" in event:
            snap["complete"] = True
    if job["future"].done():
        snap["complete"] = True
        try:
            job["future"].result()
        except Exception:
            if len(snap["errors"]) < 3:
                snap["errors"].append("FYERS option chain worker stopped unexpectedly.")
        st.session_state.pop("fno_fyers_chain_job", None)
        updated = True
    if updated:
        st.rerun(scope="app")


def fyers_chain_calls(available, expiry: str, *, force=False,
                      page="home") -> dict:
    access = connected_fyers()
    if not access:
        return {}
    symbols = sorted({x.entry.symbol for x in available if expiry in x.entry.lots})
    if not symbols:
        return {}
    key = _start_fyers_chain(access, symbols, expiry, force=force)
    _fyers_chain_heartbeat()
    snap = st.session_state.get("fno_fyers_chain_snapshot") or {}
    if snap.get("key") != key:
        st.caption("FYERS option chain · waiting for previous scan to stop…")
        return {}
    st.caption(
        f"FYERS stock option chain · {snap['processed']}/{snap['total']} checked · "
        f"{len(snap['calls'])} genuine +5% CE premiums · "
        f"{len(snap['straddles'])} ATM straddles · "
        f"{'complete' if snap['complete'] else 'scanning'}"
    )
    if snap.get("errors"):
        with st.expander("FYERS scan diagnostics", expanded=False):
            for error in snap["errors"]:
                st.caption(error)
    if st.button("↻ Refresh FYERS option chains", key=f"fyers_chain_refresh_{page}",
                 disabled=bool(st.session_state.get("fno_fyers_chain_job"))):
        _start_fyers_chain(access, symbols, expiry, force=True)
        st.rerun()
    if st.session_state.get("fno_fyers_chain_job"):
        if st.button("■ Stop FYERS scan", key=f"fyers_chain_stop_{page}"):
            st.session_state["fno_fyers_chain_job"]["cancel"].set()
    return snap.get("calls") or {}


def render_fyers_straddles(available, expiry: str):
    snap = st.session_state.get("fno_fyers_chain_snapshot") or {}
    values = snap.get("straddles") or {}
    stocks = {x.entry.symbol: x.entry.lots.get(expiry) for x in available}
    rows = []
    for ticker, x in values.items():
        if ticker not in stocks or not stocks[ticker]:
            continue
        rows.append({
            "Stock": ticker, "ATM strike (₹)": float(x.strike),
            "Cash LTP (₹)": float(x.underlying_ltp),
            "CE LTP (₹)": float(x.call_ltp), "PE LTP (₹)": float(x.put_ltp),
            "Straddle (%)": float(x.premium_pct),
            "CE+PE (₹)": float(x.call_ltp + x.put_ltp),
            "Gross/lot (₹)": float((x.call_ltp + x.put_ltp) * stocks[ticker]),
        })
    st.markdown("#### FYERS ATM straddles · same-snapshot CE + PE")
    if rows:
        frame = pd.DataFrame(rows).sort_values("Straddle (%)", ascending=False)
        st.dataframe(frame, hide_index=True, use_container_width=True,
                     height=min(470, 48 + len(frame) * 37))
    else:
        st.caption("Waiting for a real, matching FYERS CE+PE strike. No synthetic premiums.")
    st.caption("FYERS LTP may be stale outside market hours; not executable quotes.")
