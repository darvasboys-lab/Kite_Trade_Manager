"""Automatic F&O list + Dhan/Yahoo read-only price sources.

No file upload, no order endpoints, no exchange-credential persistence.
F&O list starts fetching in a worker and survives restarts as public-only cache.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import csv
import io
import os
from queue import Empty, SimpleQueue
from threading import Event

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
from dhan_visual.nse_eod_ce import MAX_UPLOAD_BYTES, NseEodError, parse_nse_fo_eod
from dhan_visual.ce_eod_local import NSE_FO_NAME, recent_nse_fo_reports
from dhan_visual.ce_eod_remote import EodDownloadResult, IST, fetch_published_nse_eod
from dhan_visual.ce_tejhq import TejEodCall, scan_tej_eod
from dhan_visual.dhan_readonly_check import check_dhan_data_plan, DhanProfileError
from dhan_visual.dhan_integration_state import (
    verified_account, token_still_current, load_manual_token,
    refresh_verified_token, save_manual_token,
)
from dhan_visual.technical_ui import technical_values_for_table, technical_visible_stage, attach_technical_columns
from dhan_visual.technical_worker import read_cached_technicals
from dhan_visual.table_layout import (
    autofit_column_config, autofit_enabled, click_autofit, dataframe_key,
)
from dhan_visual.tradingview_links import (
    clickable_stock_frame, TRADINGVIEW_SYMBOL_DISPLAY,
)
from dhan_visual.r1_today_panel import render_r1_today_panel


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
    """One-time broker onboarding, then a compact status with token renewal.

    Green check represents *completed verified setup*, not a claim that a
    time-limited Dhan token is valid forever. Only explicit button clicks
    authenticate or store a manual token. No calls to order endpoints.
    """
    saved = read_client_id()
    default_id = os.environ.get("DHAN_CLIENT_ID", "") or saved
    current_id = str(
        st.session_state.get("dhan_local_cid") or default_id or ""
    ).strip()
    verified = verified_account(current_id)
    existing_manual = str(
        st.session_state.get("dhan_local_access_token")
        or os.environ.get("DHAN_ACCESS_TOKEN")
        or ""
    ).strip()
    cached_manual = load_manual_token(current_id) if valid_client_id(current_id) else ""
    available_token = existing_manual or cached_manual
    auto_available = bool(valid_client_id(current_id) and is_configured(current_id))
    # Avoid showing the complete onboarding form once the broker has passed
    # both the plan/profile check AND one real option-chain test.
    if verified is not None:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:14px;'
            'border:1px solid #87cdaa;border-radius:12px;'
            'background:#e9f8f0;padding:14px 18px;margin:10px 0">'
            '<span role="img" aria-label="Connection verified" '
            'style="font-size:48px;line-height:1;color:#117b51;'
            'font-weight:800">✓</span>'
            '<div><strong style="color:#116644;font-size:20px">'
            'Dhan integration completed</strong>'
            '<div style="font-size:12px;color:#256f50">'
            'Verified one-stock option-chain test · '
            'Setup saved on this PC · Read-only</div></div></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Verified {verified.symbol} · expiry {verified.expiry} · "
            f"on {verified.verified_at[:10]}. "
            "The setup is remembered; no full reintegration is needed."
        )
        if not token_still_current(verified):
            st.warning(
                "Dhan access token needs renewal. The integration is already "
                "complete: only update the temporary token below. "
                "Dhan tokens expire after 24 hours."
            )
        elif not (available_token or auto_available):
            st.info(
                "Dhan is integrated, but there is no usable token in this "
                "browser session. Update only the access token below."
            )
        else:
            st.caption(
                "Dhan setup remembered. Live data still depends on the "
                "current token and Data API entitlement."
            )
    else:
        st.info(
            "Connect Dhan once: enter credentials locally, check the Data API "
            "plan, then verify one stock's ATM straddle and 5%+ CE. "
            "After success, a large green tick replaces this setup prompt."
        )

    setup_open = verified is None or (
        not (available_token or auto_available) or
        not token_still_current(verified)
    )
    with st.expander(
        "Dhan setup · one-time (read-only)" if verified is None
        else "Dhan access · update token / manage connection",
        expanded=setup_open,
    ):
        st.caption(
            "Enter credentials only on this PC, never in chat. "
            "A Client ID is remembered locally. Access tokens are not saved "
            "to project files or GitHub."
        )
        cid = st.text_input(
            "Dhan Client ID", value=default_id,
            key="dhan_local_cid",
            help="Numeric Client ID. Saved locally in .dhan_cache after entry.",
        )
        current_id = cid.strip()
        if valid_client_id(current_id):
            if saved != current_id and not save_client_id(current_id):
                st.warning("Could not save Client ID locally")
        elif current_id:
            st.warning("Enter a numeric Dhan Client ID")

        manual = st.text_input(
            "Dhan access token",
            value=os.environ.get("DHAN_ACCESS_TOKEN", ""),
            type="password",
            key="dhan_local_access_token",
            help="Get a current token from Dhan Web. "
                 "This is a password field; no access token is sent to ChatGPT.",
        ).strip()
        remember = st.checkbox(
            "Remember the current token in Windows Credential Manager "
            "(only until its expiry)",
            value=False, key="dhan_remember_manual_token",
            help="Optional. Saves ONLY after both one-time checks succeed, "
                 "or after a verified token-only renewal. "
                 "A manual token cannot work after its 24-hour expiry.",
        )
        if verified is not None:
            if st.button(
                "Verify renewed token · skip full setup",
                key="dhan_renew_token_only",
                help="Read-only Dhan Profile check; never rescans options.",
            ):
                renewal_token = manual or os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
                if not valid_client_id(current_id) or not renewal_token:
                    st.error("Enter your Client ID and a newly issued token.")
                else:
                    try:
                        profile = check_dhan_data_plan(current_id, renewal_token)
                        if not profile.data_ready:
                            st.error("Dhan Data API subscription is not active.")
                        else:
                            refresh_verified_token(current_id, profile.token_validity)
                            if remember:
                                save_manual_token(
                                    current_id, renewal_token, profile.token_validity
                                )
                            st.success("Token checked. Existing integration retained.")
                            st.rerun()
                    except (DhanProfileError, ValueError, OSError, AutoAuthError) as exc:
                        st.error(str(exc))
            st.caption(
                "Renewal checks the token and Data API plan only. "
                "No second integration or option-chain test is required."
            )

    # Keep the existing opt-in TOTP manager. It may issue a new broker token,
    # so its existing interference warning remains and user consent is required.
    current_id = current_id.strip()
    automatic = _auto_login_panel(current_id, manual)
    secure = load_manual_token(current_id) if valid_client_id(current_id) else ""
    return current_id, (manual or automatic or secure)



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
    cached_technicals = read_cached_technicals()
    queue = SimpleQueue()
    if force:
        st.session_state.pop("fno_yahoo_snapshot", None)
    st.session_state["fno_yahoo_job"] = {
        "key": key, "queue": queue,
        "future": _market_pool().submit(
            load_yahoo_all, list(universe), queue, earlier, expiry, 25,
            technical_levels=cached_technicals,
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


def _price_panel(universe, expiry, available, provider, manual, credentials=None):
    """Quote-source controls live in the compact top-right toolbar."""
    all_symbols = {row.entry.symbol for row in universe}
    if provider == "Dhan":
        cid, token = credentials if credentials is not None else _dhan_credentials()
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


@st.cache_data(show_spinner=False, max_entries=3)
def _parse_ce5_upload(payload: bytes):
    """Keep full-universe archive parsing out of routine Streamlit reruns."""
    return parse_nse_fo_eod(payload)


@st.cache_data(show_spinner=False, max_entries=5)
def _read_local_ce5_file(path_string: str, size: int, mtime_ns: int):
    """Cached local validation: one official-looking file, zero web requests."""
    path = Path(path_string)
    filename = NSE_FO_NAME.fullmatch(path.name)
    if not filename or path.is_symlink() or not path.is_file():
        raise NseEodError("Unrecognized NSE daily file")
    actual = path.stat()
    if (actual.st_size != size or actual.st_mtime_ns != mtime_ns
            or not 0 < size <= MAX_UPLOAD_BYTES):
        raise NseEodError("The NSE daily file changed or exceeds size limits")
    result = parse_nse_fo_eod(path.read_bytes())
    expected = datetime.strptime(filename.group(1), "%Y%m%d").date().isoformat()
    if result.report_date != expected:
        raise NseEodError("The report's business date does not match its filename")
    return result


@st.cache_resource
def _eod_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="nse-published-ce-eod")


def _eod_start():
    """One official EOD request per IST day / session unless user retries."""
    key = datetime.now(IST).date().isoformat()
    running = st.session_state.get("fno_ce5_remote_job")
    if running and not running["future"].done():
        return
    if st.session_state.get("fno_ce5_remote_attempt_key") == key:
        return
    st.session_state["fno_ce5_remote_attempt_key"] = key
    st.session_state["fno_ce5_remote_job"] = {
        "key": key,
        "future": _eod_pool().submit(fetch_published_nse_eod),
    }


@st.fragment(run_every="3s")
def _eod_heartbeat():
    """Refresh only after a finished read-only fetch, never on every rerun."""
    job = st.session_state.get("fno_ce5_remote_job")
    if not job:
        return
    if job["future"].done():
        try:
            result = job["future"].result()
        except Exception as exc:
            result = EodDownloadResult(
                None, "", f"NSE EOD worker failed ({type(exc).__name__})", ()
            )
        if job["key"] == st.session_state.get("fno_ce5_remote_attempt_key"):
            st.session_state["fno_ce5_remote_snapshot"] = result
        st.session_state.pop("fno_ce5_remote_job", None)
        st.rerun(scope="app")
    else:
        st.caption("NSE EOD · checking the latest published report in the background…")


@st.cache_resource
def _tej_eod_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="tejhq-eod-ce")


@st.fragment(run_every="3s")
def _tej_eod_heartbeat():
    """Drain only local worker events; no Streamlit calls from background jobs."""
    job = st.session_state.get("fno_tej_eod_job")
    if not job:
        return
    state = st.session_state.get("fno_tej_eod_state")
    if not state or state.get("month") != job["month"]:
        job["cancel"].set()
        return
    updated = False
    while True:
        try:
            event = job["events"].get_nowait()
        except Empty:
            break
        kind = event[0]
        if kind == "quote":
            _, symbol, quote = event
            if state["asof"] is None:
                state["asof"] = quote.report_date
            if quote.report_date == state["asof"]:
                state["quotes"][symbol] = quote
                state["processed"] += 1
                updated = True
        elif kind == "error":
            _, symbol, error = event
            state["errors"][symbol] = error
            state["processed"] += 1
            updated = True
        else:
            state["status"] = event[1]
            updated = True
    if job["future"].done():
        try:
            job["future"].result()
        except Exception:
            state["status"] = "Worker ended unexpectedly (no credentials logged)"
        st.session_state.pop("fno_tej_eod_job", None)
        updated = True
    if updated:
        st.rerun(scope="app")
    else:
        st.caption(
            f"TejHQ EOD scan: {state['processed']}/{state['total']} processed · "
            f"{len(state['quotes'])} CE values"
        )


def _tejhq_eod_panel(available, expiry):
    """Optional paid provider, never called unless user presses Scan."""
    with st.expander("Alternative EOD feed · TejHQ API (optional paid)", expanded=False):
        st.caption(
            "NSEFNO is free to view in a browser but prohibits automated "
            "bulk scraping without permission. TejHQ offers a documented "
            "Retail/Pro F&O EOD API (subscription required). "
            "This app does not purchase a plan or send your key to NSE/Dhan."
        )
        st.markdown("[TejHQ F&O access and pricing](https://tejhq.dev/fo)")
        st.markdown("[View NSEFNO options manually](https://nsefno.com/pages/historical_options)")
        token = st.text_input(
            "TejHQ Retail/Pro API key", type="password",
            key="fno_tej_eod_key",
            help="Only sent via HTTPS Authorization header to api.tejhq.dev "
                 "after you click Scan. Not stored to disk, CSV or GitHub.",
        )
        job = st.session_state.get("fno_tej_eod_job")
        busy = bool(job and not job["future"].done())
        state = st.session_state.get("fno_tej_eod_state")
        if state is None or state.get("month") != expiry:
            if job:
                job["cancel"].set()
            state = {
                "month": expiry, "quotes": {}, "errors": {},
                "asof": None, "processed": 0, "total": 0,
                "status": "Not started",
            }
            st.session_state["fno_tej_eod_state"] = state
        cols = st.columns([2, 1])
        with cols[0]:
            start = st.button(
                "Scan all stock CE EOD (TejHQ)", key="fno_tej_eod_scan",
                disabled=busy or len(token) < 12 or not available,
                help="Reads every symbol one at a time, up to 250 calls. "
                     "Requires a paid Retail/Pro API key.",
                use_container_width=True,
            )
        with cols[1]:
            if st.button(
                "Stop", key="fno_tej_eod_stop",
                disabled=not busy, use_container_width=True,
            ) and job:
                job["cancel"].set()
        if start:
            events, cancel = SimpleQueue(), Event()
            symbols = [item.entry.symbol for item in available]
            state.update(
                quotes={}, errors={}, asof=None, processed=0,
                total=len(symbols), status="Scanning",
            )
            st.session_state["fno_tej_eod_job"] = {
                "month": expiry, "events": events, "cancel": cancel,
                "future": _tej_eod_pool().submit(
                    scan_tej_eod, symbols, expiry, token, events, cancel,
                ),
            }
            st.rerun()
        st.caption(
            f"TejHQ: {state['status']} · {state['processed']}/{state['total']} "
            f"checked · {len(state['quotes'])} CE closes · "
            f"report {state['asof'] or 'not available'}"
        )
        if state["errors"]:
            st.caption(
                "Sample failures: " + "; ".join(
                    f"{symbol}: {err}"
                    for symbol, err in list(state["errors"].items())[:2]
                )
            )
        if job:
            _tej_eod_heartbeat()
    return state


def _free_ce5_panel(expiry, dhan_count=0, available=None):
    """Explicit EOD/Live choice: never silently substitute one for another."""
    mode = st.radio(
        "CE ≥+5% data", ["EOD", "Live"], horizontal=True,
        key="fno_ce5_mode", index=0,
        help="EOD: official NSE F&O daily close (free). Live: opt-in Dhan "
             "option-chain LTP. No Yahoo options are fabricated.",
    )
    if mode == "Live":
        st.caption(
            f"LIVE CE · {dhan_count} Dhan option-chain snapshots available. "
            "To populate this column, run the opt-in full option-chain scan below. "
            "No EOD price is substituted for live data."
        )
        return {}, ""

    _eod_start()
    _eod_heartbeat()
    candidates = []
    remote = st.session_state.get("fno_ce5_remote_snapshot")
    if remote and remote.snapshot:
        candidates.append((remote.snapshot, "Official NSE archive (automatic)"))
    local_errors = []
    for path in recent_nse_fo_reports():
        try:
            metadata = path.stat()
            local = _read_local_ce5_file(
                str(path), metadata.st_size, metadata.st_mtime_ns,
            )
            candidates.append((local, f"Downloads/{path.name}"))
            break
        except (NseEodError, OSError, ValueError) as exc:
            local_errors.append(f"{path.name}: {type(exc).__name__}")

    with st.expander("Free NSE F&O EOD · report and diagnostics", expanded=False):
        if remote:
            st.caption(remote.message)
            if remote.checked:
                st.caption("Archive dates checked: " + ", ".join(remote.checked))
        st.caption(
            "EOD mode downloads a publicly published, date-specific official "
            "NSE F&O UDiFF daily ZIP when the server permits access. "
            "A 403/429 is NOT bypassed. If blocked, place the official file "
            "in your existing Downloads folder or upload it here."
        )
        st.markdown(
            "[Open NSE official derivatives reports]"
            "(https://www.nseindia.com/all-reports-derivatives)"
        )
        if st.button(
            "↻ Refresh official EOD data", key="fno_ce5_recheck_downloads",
            help="Check Downloads and make one new bounded attempt at the "
                 "latest official archive, not a live option-chain request.",
        ):
            st.session_state.pop("fno_ce5_remote_attempt_key", None)
            st.session_state.pop("fno_ce5_remote_snapshot", None)
            _eod_start()
            st.rerun()
        uploaded = st.file_uploader(
            "Optional: choose NSE F&O daily ZIP or CSV",
            type=["zip", "csv"],
            key="fno_ce5_nse_eod_file",
            help="Use the official daily report if archive download is blocked.",
        )
        if uploaded is not None:
            try:
                candidates.append((_parse_ce5_upload(uploaded.getvalue()),
                                   f"Uploaded {uploaded.name}"))
            except NseEodError as exc:
                st.error(f"Uploaded report could not be read: {exc}")
        if local_errors:
            st.caption("Downloads file diagnostics: " + "; ".join(local_errors[:2]))

    # Optional alternative must stay user-initiated and never request an
    # API before a valid paid key and explicit Scan action.
    tej_state = _tejhq_eod_panel(available or [], expiry)
    best_nse_date = max((snap.report_date for snap, _ in candidates), default="")
    if (tej_state.get("quotes") and
            (not best_nse_date or tej_state["asof"] > best_nse_date)):
        count = len(tej_state["quotes"])
        st.caption(
            f"EOD · TejHQ {tej_state['asof']} · {count} CE references "
            "(Retail/Pro, selected expiry, report-date matched)."
        )
        return tej_state["quotes"], tej_state["asof"]

    if not candidates:
        if not remote:
            st.caption("EOD CE · checking the official exchange archive…")
        else:
            st.warning(
                f"No valid NSE EOD CE data for {expiry}. {remote.message} "
                "Use the official report link in the panel to import a ZIP."
            )
        return {}, ""

    # Prefer the newest dated official report; never replace a recent EOD
    # quote with an older local upload. Choose one report for all stocks.
    selected, label = max(candidates, key=lambda pair: pair[0].report_date)
    calls, report_day = selected.quotes.get(expiry, {}), selected.report_date
    if calls:
        untraded = sum(q.volume == 0 for q in calls.values())
        st.caption(
            f"EOD · NSE {report_day} · {len(calls)} December/selected-month CE "
            f"references · {untraded} untraded (marked, not live). Source: {label}"
        )
    else:
        st.warning(
            f"NSE report {report_day} contains no valid 5%+ CE closing prices "
            f"for {expiry}; check report and contract month."
        )
    return calls, report_day


def _ce5_choice(symbol, dhan_calls, eod_calls, eod_date, mode="EOD"):
    """Return only the explicitly selected CE price type, never a silent mix."""
    if mode == "Live":
        if symbol in dhan_calls:
            quote = dhan_calls[symbol]
            return quote, f"Dhan LTP snapshot · {quote.captured_at}"
        return None, "Live unavailable · Dhan scan required"
    if mode != "EOD":
        raise ValueError("Unknown CE mode")
    if symbol in eod_calls:
        quote = eod_calls[symbol]
        suffix = " · 0 volume (reference only)" if quote.volume == 0 else ""
        provider = "TejHQ EOD" if isinstance(quote, TejEodCall) else "NSE EOD"
        return quote, f"{provider} {eod_date}{suffix}"
    return None, "EOD unavailable · report/contract missing"


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
    # Render onboarding once for all quote modes, not only when cash quotes
    # happen to be switched from Yahoo to Dhan.
    dhan_credentials = _dhan_credentials()
    provider, all_prices = _price_panel(
        universe, expiry, available, provider, manual,
        credentials=dhan_credentials,
    )
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
    technicals = technical_values_for_table(technical_symbols, priority_quotes=prices)
    visible_stage = technical_visible_stage(technical_symbols)
    technical_state = st.session_state.get("fno_technicals") or {}
    r1_ready = (
        visible_stage == 4
        and technical_state.get("status") == "complete"
    )
    dhan_ce5 = available_otm_calls(available, expiry)
    eod_calls, eod_date = _free_ce5_panel(
        expiry, dhan_count=len(dhan_ce5), available=available,
    )
    ce_mode = st.session_state.get("fno_ce5_mode", "EOD")
    selected_ce = eod_calls if ce_mode == "EOD" else dhan_ce5
    # R1 table must use the SAME selected CE mode as the main screener.
    render_r1_today_panel(
        available, expiry, technicals, compact=compact,
        technically_ready=r1_ready, priority_quotes=prices,
        ce_calls=selected_ce,
    )
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
        # Try Dhan chain first; only then a user-uploaded NSE daily file.
        # Each ratio uses its OWN source's same-time underlying, never Yahoo CMP.
        ce5_selected = [
            _ce5_choice(symbol, dhan_ce5, eod_calls, eod_date, ce_mode)
            for symbol in frame["Symbol"]
        ]
        frame["5%+ CE premium (%)"] = [
            float(quote.premium_pct) if quote is not None else None
            for quote, _ in ce5_selected
        ]
        frame["CE source"] = [source for _, source in ce5_selected]
        config["5%+ CE premium (%)"] = st.column_config.NumberColumn(
            "CE ≥+5% (%)", format="%.2f%%", width="medium",
            help="First listed call strike >= source underlying ×1.05. "
                 "EOD (default): NSE official daily close for matching spot date; "
                 "0-volume values are explicitly flagged as untraded reference. "
                 "Live: opt-in Dhan option-chain LTP only; never mix modes.",
        )
        config["CE source"] = st.column_config.TextColumn(
            "CE data", help="Dhan request timestamp, NSE EOD report date, or Unavailable.",
            width="medium",
        )
        for title, note in (
            ("EMA 200", "Daily 200-session EMA"),
            ("EMA 50", "Daily 50-session EMA"),
            ("EMA 20", "Daily 20-session EMA"),
            ("EMA 10", "Daily 10-session EMA"),
            ("Monthly R1", "Current calendar month's standard R1, calculated from previous completed month's H/L/C"),
        ):
            config[title] = st.column_config.TextColumn(title, help=note, width="medium")
        # The source Symbol remains untouched for filters, prices, sorting,
        # EMA/R1 matches, and CSV. Show a linked NSE ticker in its place.
        # No extra visible company/name/URL column is added.
        display_frame = clickable_stock_frame(
            frame, source="Symbol", target="TradingView chart",
        )
        config["Symbol"] = None  # Hide raw ticker; link displays the name.
        config["TradingView chart"] = st.column_config.LinkColumn(
            "Stock",
            display_text=TRADINGVIEW_SYMBOL_DISPLAY,
            help="Click the NSE stock symbol to open its TradingView chart.",
            width="small",
        )
        # Positive EMA signals get distinct, softly tinted checkbox cells.
        # This is display-only; raw technical values and CSV are unchanged.
        ema_checkbox_palette = {
            "EMA 200": ("#916900", "#FFF3BD"),  # yellow
            "EMA 50": ("#B43E87", "#FCE1F0"),   # pink
            "EMA 20": ("#C75E62", "#FDE5E5"),   # light red
            "EMA 10": ("#16845E", "#DCF5E8"),   # green
        }
        for ema_header in ema_checkbox_palette:
            display_frame[ema_header] = display_frame[ema_header].str.replace(
                "✅", "☑", n=1, regex=False,
            )
        styled_frame = display_frame.style
        for ema_header, (ink, tint) in ema_checkbox_palette.items():
            styled_frame = styled_frame.apply(
                lambda values, ink=ink, tint=tint: [
                    f"color: {ink}; background-color: {tint}; font-weight: 700"
                    if isinstance(value, str) and value.startswith("☑") else ""
                    for value in values
                ],
                subset=[ema_header],
            )
        st.dataframe(
            styled_frame, use_container_width=True, hide_index=True, height=580,
            key=dataframe_key("ccs_stock_screener", st.session_state),
            column_config=autofit_column_config(
                config, autofit_enabled(st.session_state),
            ),
        )
    st.caption(
        f"{provider} · {filled}/{len(available)} prices · "
        "☑ colored EMA = above EMA · ✅ above R1 · no tick = at/below · — waiting/unavailable · "
        "Yahoo indicators reveal EMA200 → EMA50 → EMA20 → EMA10 → R1 · "
        f"CE ≥5% mode: {ce_mode} (EOD official close / Live Dhan LTP; never mixed)"
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
    render_straddle_panel(available, expiry,
                          priority_levels=technicals, priority_quotes=prices)
    render_atm_panel(available, rows, expiry)
    if rows:
        # Export fully computed cached levels without waiting for visual
        # stage animation; no extra Yahoo requests are made.
        exported = attach_technical_columns(
            pd.DataFrame(rows).drop(columns=["Company"], errors="ignore"),
            technicals, stage=4,
        )
        export_selected = [
            _ce5_choice(sym, dhan_ce5, eod_calls, eod_date, ce_mode)
            for sym in exported["Symbol"]
        ]
        exported["5%+ CE premium (%)"] = [
            float(quote.premium_pct) if quote is not None else None
            for quote, _ in export_selected
        ]
        exported["5%+ CE strike (₹)"] = [
            float(quote.strike) if quote is not None else None
            for quote, _ in export_selected
        ]
        exported["Dhan CMP for CE % (₹)"] = [
            float(dhan_ce5[sym].underlying_ltp)
            if ce_mode == "Live" and sym in dhan_ce5 else None
            for sym in exported["Symbol"]
        ]
        exported["EOD underlying for CE % (₹)"] = [
            float(quote.underlying_close)
            if source.startswith(("NSE EOD", "TejHQ EOD")) else None
            for quote, source in export_selected
        ]
        exported["CE source"] = [source for _, source in export_selected]
        exported["Indicators as of"] = [
            technicals[sym].asof if sym in technicals else ""
            for sym in exported["Symbol"]
        ]
        st.download_button(
            "Download filtered CSV", data=exported.to_csv(index=False),
            file_name=f"fno_stock_values_{expiry}_{provider.lower().replace(' ', '_')}.csv",
            mime="text/csv", key="fno_export",
        )
