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
from dhan_visual.option_panel import render_atm_panel, _local_creds
from dhan_visual.straddle_panel import render_straddle_panel, available_otm_calls
from dhan_visual.nse_eod_ce import MAX_UPLOAD_BYTES, NseEodError, parse_nse_fo_eod
from dhan_visual.ce_eod_local import NSE_FO_NAME, recent_nse_fo_reports
from dhan_visual.ce_eod_remote import EodDownloadResult, IST, fetch_published_nse_eod
from dhan_visual.ce_tejhq import TejEodCall, scan_tej_eod
from dhan_visual.dhan_funds import read_dhan_balance, DhanFundsError
from dhan_visual.quote_routing import (
    AUTO_SOURCE, choose_cash_source, merge_dhan_yahoo,
    missing_underlyings, should_fetch_again, nse_regular_session,
)
from dhan_visual.dhan_readonly_check import check_dhan_data_plan, DhanProfileError
from dhan_visual.dhan_integration_state import (
    verified_account, token_still_current, load_manual_token,
    refresh_verified_token, save_manual_token, parse_dhan_token_expiry,
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
            st.session_state["dhan_auto_access_cid"] = cid
            return
    st.session_state.pop("dhan_auto_error", None)
    st.session_state["dhan_auto_auth_job"] = {
        "cid": cid,
        "future": _market_pool().submit(load_or_generate, cid),
    }


def _schedule_auto_profile_check(cid: str, token: str) -> None:
    """One read-only profile check per valid auto-token, asynchronously."""
    if not valid_client_id(cid) or not isinstance(token, str) or len(token) < 16:
        return
    fingerprint = sha256(token.encode()).hexdigest()
    entry = st.session_state.get("fno_dhan_profile_status") or {}
    if (entry.get("cid") == cid and
            entry.get("token_fingerprint") == fingerprint and
            _confirmed_dhan_profile(cid, token) is not None):
        return
    job = st.session_state.get("dhan_auto_profile_job")
    if job is not None:
        # Never overlap two profile requests or show another account's result.
        if not job["future"].done():
            return
        # The heartbeat must consume the finished job before scheduling more.
        return
    if st.session_state.get("dhan_auto_profile_attempt") == (cid, fingerprint):
        return
    st.session_state["dhan_auto_profile_attempt"] = (cid, fingerprint)
    st.session_state["dhan_auto_profile_job"] = {
        "cid": cid, "fingerprint": fingerprint,
        "future": _market_pool().submit(check_dhan_data_plan, cid, token),
    }


@st.fragment(run_every="15s")
def _auto_login_heartbeat(cid):
    """Process saved TOTP login/profile jobs and renew expiring tokens."""
    if not is_configured(cid):
        return
    profile_job = st.session_state.get("dhan_auto_profile_job")
    if profile_job and profile_job["future"].done():
        st.session_state.pop("dhan_auto_profile_job", None)
        active = st.session_state.get("dhan_auto_access")
        matching = bool(
            profile_job["cid"] == cid
            and isinstance(active, AutoAccess)
            and active.usable()
            and st.session_state.get("dhan_auto_access_cid") == cid
            and profile_job["fingerprint"] ==
            sha256(active.token.encode()).hexdigest()
        )
        if matching:
            try:
                profile = profile_job["future"].result()
                st.session_state["fno_dhan_profile_status"] = {
                    "cid": cid, "token_fingerprint": profile_job["fingerprint"],
                    "status": profile,
                }
                st.session_state.pop("dhan_auto_error", None)
                if profile.data_ready:
                    st.session_state["fno_activate_live_on_next_run"] = True
                    st.session_state["fno_activate_auto_quote_on_next_run"] = True
                else:
                    st.session_state["dhan_auto_error"] = (
                        "Dhan auto-login succeeded, but the Data API plan is "
                        "inactive. Check your subscription in Settings."
                    )
            except DhanProfileError as exc:
                st.session_state["dhan_auto_error"] = (
                    "Dhan token generated, but Data API profile check failed: "
                    + str(exc)
                )
        st.rerun(scope="app")
    job = st.session_state.get("dhan_auto_auth_job")
    if job:
        if job["future"].done():
            try:
                access = job["future"].result()
                if job["cid"] == cid and is_configured(cid):
                    st.session_state["dhan_auto_access"] = access
                    st.session_state["dhan_auto_access_cid"] = cid
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


def _auto_login_panel(cid, manual_token, *, allow_login=True):
    """Optional local TOTP setup, independent of ResultBot's secrets."""
    if not valid_client_id(cid):
        return ""
    enabled = is_configured(cid)
    with st.expander(
        "Remember Dhan login securely · PIN + TOTP (optional)",
        expanded=not enabled and not bool(manual_token),
    ):
        if not enabled:
            st.caption(
                "To avoid entering a new access token each day, save your "
                "Dhan PIN and permanent TOTP setup secret here ONCE. "
                "Windows Credential Manager holds them locally. "
                "A fresh access token still has 24-hour validity."
            )
        if not enabled:
            st.warning(
                "Issuing another Dhan token may disrupt other trading sessions. "
                "Enable automatic renewal only if you accept this risk."
            )
        if enabled:
            st.success("✓ Secure one-time login saved in Windows Credential Manager.")
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

    if not enabled:
        return ""
    # A previously pasted, expired manual token must NEVER suppress the
    # saved PIN/TOTP flow. Auto-login is opt-in via the Enable button above.
    if not allow_login:
        # Opening a Yahoo/EOD dashboard must not issue broker tokens.
        old = read_cached_token(cid)
        return old.token if old else ""
    stored = st.session_state.get("dhan_auto_access")
    if (st.session_state.get("dhan_auto_access_cid") != cid
            or not isinstance(stored, AutoAccess) or not stored.usable()):
        stored = read_cached_token(cid)
        if stored:
            st.session_state["dhan_auto_access"] = stored
            st.session_state["dhan_auto_access_cid"] = cid
        else:
            _start_auto_login(cid)
    _auto_login_heartbeat(cid)
    if stored and stored.usable() and allow_login:
        _schedule_auto_profile_check(cid, stored.token)
    if st.session_state.get("dhan_auto_error"):
        st.error(st.session_state["dhan_auto_error"])
        if st.button("Retry Dhan TOTP login", key="dhan_auto_retry"):
            _start_auto_login(cid, force=True)
    if stored and stored.usable():
        st.caption(
            "✓ Automatic Dhan token active until "
            + stored.expires.astimezone().strftime("%d %b %Y %H:%M")
        )
        if st.session_state.get("dhan_auto_profile_job"):
            st.caption("Checking the broker Data API plan in the background…")
        return stored.token
    return ""


def _confirmed_dhan_profile(cid, token):
    """Recognize only an explicitly checked, same-account, same-token profile.

    The profile check is never a background login and cannot authenticate a
    different token or account. Its token expiration also bounds reuse.
    """
    if not valid_client_id(cid) or not isinstance(token, str) or len(token) < 16:
        return None
    entry = st.session_state.get("fno_dhan_profile_status") or {}
    if (entry.get("cid") != cid or
            entry.get("token_fingerprint") != sha256(token.encode()).hexdigest()):
        return None
    profile = entry.get("status")
    if profile is None:
        return None
    expiry = parse_dhan_token_expiry(profile.token_validity)
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        return None
    return profile


def _dhan_credentials(*, need_market=False):
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
    cached_auto = read_cached_token(current_id) if auto_available else None
    # A freshly generated Dhan TOTP token may be valid even when the
    # original setup-time manual token has expired.
    session_auto = st.session_state.get("dhan_auto_access")
    auto_current = bool(
        (isinstance(cached_auto, AutoAccess) and cached_auto.usable())
        or (
            st.session_state.get("dhan_auto_access_cid") == current_id
            and isinstance(session_auto, AutoAccess)
            and session_auto.usable()
        )
    )
    needs_token = bool(
        verified is not None and not (
            token_still_current(verified) or auto_current
        )
    )
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
        if needs_token:
            st.warning(
                "Dhan token expired. The setup remains verified; renew the "
                "token or use your saved secure TOTP settings."
            )
        elif not (available_token or auto_available):
            st.info(
                "Dhan is integrated, but there is no usable token in this "
                "browser session. Update only the access token below."
            )
        # Verified setup is durable. The token and market data are not.
    else:
        if not st.session_state.get("fno_first_dhan_hint_seen"):
            st.toast(
                "Dhan setup is available below. Verify it once to enable "
                "automatic Live CE and secure credential reuse.", icon="🔐"
            )
            st.session_state["fno_first_dhan_hint_seen"] = True

    connection_token = available_token
    if not connection_token and auto_current:
        if (st.session_state.get("dhan_auto_access_cid") == current_id
                and isinstance(session_auto, AutoAccess) and session_auto.usable()):
            connection_token = session_auto.token
        elif isinstance(cached_auto, AutoAccess) and cached_auto.usable():
            connection_token = cached_auto.token
    connected_profile = _confirmed_dhan_profile(current_id, connection_token)
    if verified is None and connected_profile is not None:
        st.success("✓ Dhan connected · account identity verified (read-only)")
        if not connected_profile.data_ready:
            st.warning("Dhan Data API plan inactive · option chains unavailable.")
        else:
            st.caption("Data API ACTIVE · verify one stock below to activate Live CE.")

    setup_open = verified is None or (
        not (available_token or auto_available) or needs_token
    )
    with st.expander(
        "Dhan setup · one-time (read-only)" if verified is None
        else "Dhan access · update token / manage connection",
        expanded=setup_open,
    ):
        if not st.session_state.get("fno_vault_hint_seen"):
            st.toast(
                "Use your own PC for Dhan credentials. Tokens and TOTP "
                "secrets can be saved in Windows Credential Manager only.",
                icon="🔒",
            )
            st.session_state["fno_vault_hint_seen"] = True
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
            "Securely remember this verified token on this Windows PC "
            "(until expiry)",
            value=True, key="dhan_remember_manual_token",
            help="Optional secure storage in Windows Credential Manager "
                 "after you click Connect, or after token renewal. "
                 "A manual token still expires after about 24 hours.",
        )
        # Profile authentication is the first explicit step. Unlike the
        # one-stock option test, it can show the Dhan funds balance immediately.
        if verified is None:
            candidate = (
                manual or cached_manual
                or (
                    session_auto.token
                    if (st.session_state.get("dhan_auto_access_cid") == current_id
                        and isinstance(session_auto, AutoAccess)
                        and session_auto.usable())
                    else ""
                )
                or (cached_auto.token if isinstance(cached_auto, AutoAccess)
                    and cached_auto.usable() else "")
            )
            profile_ok = _confirmed_dhan_profile(current_id, candidate)
            if profile_ok is None:
                if st.button(
                    "Connect to Dhan", key="dhan_connect_button",
                    type="primary", use_container_width=True,
                    help="Read-only GET /v2/profile. No orders, no new token.",
                ):
                    if not valid_client_id(current_id) or len(candidate) < 16:
                        st.error("Enter a valid Client ID and Dhan access token.")
                    else:
                        with st.spinner("Verifying Dhan account…"):
                            try:
                                profile = check_dhan_data_plan(current_id, candidate)
                                st.session_state["fno_dhan_profile_status"] = {
                                    "cid": current_id, "status": profile,
                                    "token_fingerprint": sha256(
                                        candidate.encode()
                                    ).hexdigest(),
                                }
                                if remember and manual:
                                    try:
                                        save_manual_token(
                                            current_id, candidate,
                                            profile.token_validity,
                                        )
                                    except AutoAuthError:
                                        st.warning(
                                            "Account connected, but the token could "
                                            "not be saved securely. Check token "
                                            "validity or Windows Credential Manager."
                                        )
                                st.session_state["fno_activate_live_on_next_run"] = True
                                st.rerun()
                            except DhanProfileError as exc:
                                st.error(str(exc))
            else:
                st.caption(
                    "✓ Connection verified. Balance loads above. "
                    "Next, verify one option chain below to complete setup."
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
    old_manual_expired = bool(
        verified is not None and not token_still_current(verified)
    )
    automatic = _auto_login_panel(
        current_id,
        "" if old_manual_expired else manual,
        allow_login=need_market,
    )
    secure = load_manual_token(current_id) if valid_client_id(current_id) else ""
    # A previously verified (but now expired) manual token cannot trigger
    # automatic quote calls. The explicit token-only renewal button above
    # remains available, and an independently current auto token can work.
    if old_manual_expired:
        return current_id, (automatic or secure)
    return current_id, (manual or automatic or secure)



def _universe_key(universe):
    # The same cash stock is requested exactly once, regardless of expiry.
    symbols = ",".join(sorted({row.entry.symbol for row in universe}))
    return sha256(symbols.encode()).hexdigest()


def _start_dhan_prices(universe, cid, token, force=False):
    """Bounded Dhan LTP snapshots: at most one per five minutes by default."""
    identity = sha256((cid + ":" + token + ":" + _universe_key(universe)).encode()).hexdigest()
    existing = st.session_state.get("fno_dhan_job")
    if existing and not existing["future"].done():
        if existing["key"] == identity:
            return identity
        # Keep old worker out of the new credentials' display path; it will
        # finish naturally without exposing results to the new login.
    previous = st.session_state.get("fno_dhan_snapshot") or {}
    if st.session_state.get("fno_dhan_attempt_key") == identity:
        last = st.session_state.get("fno_dhan_last_attempt_at")
        if (last is None and not force
                and previous.get("key") == identity):
            # Existing valid in-memory snapshot from an earlier app version,
            # browser state or test fixture. Treat it as freshly cached
            # rather than re-requesting and replacing real cash prices.
            st.session_state["fno_dhan_last_attempt_at"] = time.monotonic()
            return identity
        if not should_fetch_again(
            last,
            now=time.monotonic(), errors=previous.get("errors") or (),
            manual=force, market_open=nse_regular_session(),
        ):
            return identity
    st.session_state["fno_dhan_attempt_key"] = identity
    st.session_state["fno_dhan_last_attempt_at"] = time.monotonic()
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
    if current and not current["future"].done():
        # Never launch overlapping Yahoo bulk scans while source/missing
        # subsets change; the first in-flight result can still fill rows.
        return current["key"]
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


@st.cache_resource
def _funds_pool():
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="dhan-funds-readonly")


@st.fragment(run_every="5s")
def _poll_dhan_funds():
    """Only the main Streamlit thread reads the worker result or updates UI."""
    job = st.session_state.get("fno_dhan_funds_job")
    if not job:
        return
    if not job["future"].done():
        st.caption("Dhan balance · updating…")
        return
    try:
        data = job["future"].result()
        snapshot = {"key": job["key"], "data": data, "error": ""}
    except DhanFundsError as exc:
        snapshot = {"key": job["key"], "data": None, "error": str(exc)}
    except Exception:
        snapshot = {
            "key": job["key"], "data": None,
            "error": "Dhan balance worker failed",
        }
    st.session_state["fno_dhan_funds_snapshot"] = snapshot
    st.session_state.pop("fno_dhan_funds_job", None)
    st.rerun(scope="app")


def _dhan_funds_panel(cid, token):
    """Top strip; a read-only GET every five minutes, never on every rerun."""
    status = verified_account(cid) if valid_client_id(cid) else None
    # Funds require only an explicitly successful, token-matched Dhan
    # profile check. Stock-option CE verification is a separate second step.
    if status is None and _confirmed_dhan_profile(cid, token) is None:
        return  # no funds request before user clicks Connect
    if len(token) < 16:
        st.metric("Dhan available balance", "—", help="Token renewal needed")
        return
    key = sha256((cid + ":" + token).encode()).hexdigest()
    job = st.session_state.get("fno_dhan_funds_job")
    prior = st.session_state.get("fno_dhan_funds_snapshot") or {}
    same = prior.get("key") == key
    previous = st.session_state.get("fno_dhan_funds_attempt") or {}
    now = time.monotonic()
    blocked = (
        same and "429" in str(prior.get("error") or "")
    )
    window = 3600 if blocked else (300 if nse_regular_session() else 1800)
    due = (
        previous.get("key") != key
        or now - previous.get("when", -1e12) >= window
    )
    if (due and
            not (job and not job["future"].done())):
        st.session_state["fno_dhan_funds_attempt"] = {
            "key": key, "when": now,
        }
        st.session_state["fno_dhan_funds_job"] = {
            "key": key,
            "future": _funds_pool().submit(read_dhan_balance, cid, token),
        }
    if same and prior.get("data") is not None:
        entry = prior["data"]
        st.metric(
            "Dhan available balance",
            f"₹{float(entry.available):,.2f}",
            help="Dhan available trading balance, from GET /v2/fundlimit. "
                 "Not an available options margin guarantee.",
        )
        st.success("✓ Balance fetched successfully")
        st.caption(f"Funds snapshot · {entry.checked_at} UTC")
    else:
        st.metric("Dhan available balance", "—")
        if same and prior.get("error"):
            st.caption("Funds: " + prior["error"])
    if st.session_state.get("fno_dhan_funds_job"):
        _poll_dhan_funds()


def _price_panel(universe, expiry, available, provider, manual, credentials=None):
    """Dhan-first by default for verified accounts; Yahoo per missing stock.

    In Auto, no Dhan requests are sent until one-time integration succeeded.
    The fallback runs only AFTER a bounded Dhan snapshot has completed or if
    no authorized Dhan token is available. Each row retains its own source.
    """
    all_symbols = {row.entry.symbol for row in universe}
    cid, token = credentials if credentials is not None else _dhan_credentials(
        need_market=True,
    )
    ready = bool(
        valid_client_id(cid) and len(token) >= 16
        and verified_account(cid) is not None
    )
    effective = choose_cash_source(
        provider, verified=ready, token_available=len(token) >= 16,
    )
    if effective == "Dhan":
        if valid_client_id(cid) and len(token) >= 16:
            identity = _start_dhan_prices(universe, cid, token, force=manual)
            snapshot = st.session_state.get("fno_dhan_snapshot") or {}
            if snapshot.get("key") != identity:
                snapshot = {}
            dhan_prices = {
                sym: value for sym, value in snapshot.get("quotes", {}).items()
                if sym in all_symbols
            }
            prices = dict(dhan_prices)
            if st.session_state.get("fno_dhan_job"):
                st.caption(f"Dhan cash LTP · fetching {len(universe)} stocks…")
            elif snapshot.get("requested_at"):
                st.caption(f"Dhan · {len(dhan_prices)} cash quotes · cached snapshot")
            for error in (snapshot.get("errors") or [])[:2]:
                st.warning(error)
        else:
            dhan_prices, prices = {}, {}
            st.warning("Dhan cash LTP unavailable · check your local token.")
        if provider == AUTO_SOURCE and not st.session_state.get("fno_dhan_job"):
            # Yahoo does not replace any actual Dhan LTP; use it only for
            # missing stocks after the broker request is complete.
            missing = missing_underlyings(universe, dhan_prices)
            if missing:
                key = _start_yahoo_prices(missing, expiry, force=manual)
                fallback = st.session_state.get("fno_yahoo_snapshot") or {}
                if fallback.get("key") == key:
                    yahoo_prices = {
                        sym: q for sym, q in fallback.get("quotes", {}).items()
                        if sym in {row.entry.symbol for row in missing}
                    }
                    prices = merge_dhan_yahoo(dhan_prices, yahoo_prices)
                else:
                    yahoo_prices = {}
                st.caption(
                    f"Auto · Dhan {len(dhan_prices)} · "
                    f"Yahoo fallback {len(yahoo_prices)} (missing only)"
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
        st.caption(
            f"Yahoo · {len(prices)}/{total} priced · "
            f"{snap.get('processed', 0)}/{total} scanned"
        )
        for error in (snap.get("errors") or [])[:2]:
            st.warning(error)
    if st.session_state.get("fno_dhan_job") or st.session_state.get("fno_yahoo_job"):
        _poll_market_data()
    return (provider if provider != AUTO_SOURCE else
            f"Auto · {effective} first"), prices


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
        # Stock cash LTP is NOT an options premium. A separate, paced Dhan
        # option-chain scan is necessary to calculate the actual 5%+ CE.
        job = st.session_state.get("fno_straddle_job")
        cid = str(st.session_state.get("dhan_local_cid") or read_client_id()).strip()
        verified = bool(valid_client_id(cid) and verified_account(cid))
        snap = (st.session_state.get("fno_straddle_snapshots") or {})
        candidates = [
            state for state in snap.values()
            if isinstance(state, dict) and state.get("month") == expiry
            and state.get("client_id") == cid
        ]
        current = max(candidates, key=lambda s: s.get("processed", 0)) if candidates else {}
        total = len(available or [])
        checked = int(current.get("processed", 0))
        errors = current.get("errors") or {}
        if job:
            st.caption(
                f"Dhan CE option chains · {checked}/{total} stocks checked · "
                f"{dhan_count} usable 5%+ CE quotes · rate-limited scan running"
            )
        elif dhan_count:
            st.caption(
                f"Dhan CE · {dhan_count}/{total} verified option premiums · "
                f"{checked} stocks checked · last snapshot, not a live stream"
            )
        elif not verified:
            st.warning(
                "CE unavailable: Dhan options integration has not yet passed "
                "the one-stock profile + option-chain check. "
                "Complete Dhan setup below; cash quotes alone are insufficient."
            )
        elif current.get("fatal"):
            st.warning(f"CE scan stopped: {current['fatal']}")
        elif current.get("started"):
            st.warning(
                f"No valid 5%+ CE prices from {checked} attempted stocks. "
                "See Unavailable stocks and quote details below."
            )
        else:
            st.info(
                "Live CE requires Dhan option-chain snapshots; these are separate "
                "from cash LTPs. Start the paced scan below."
            )
        if verified and not job:
            if st.button(
                "↻ Fetch 5%+ CE & ATM straddles",
                key="fno_ce5_fetch_live",
                help="One read-only, sequential Dhan option-chain pass; "
                     "uses the same response for 5%+ CE and ATM straddle.",
            ):
                st.session_state["fno_ce5_scan_requested"] = expiry
                st.rerun()
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


def _ce5_choice(symbol, dhan_calls, eod_calls, eod_date, mode="EOD",
                scan_pending=False):
    """Return only the explicitly selected CE price type, never a silent mix."""
    if mode == "Live":
        if symbol in dhan_calls:
            quote = dhan_calls[symbol]
            return quote, f"Dhan LTP snapshot · {quote.captured_at}"
        return None, (
            "Dhan Live · scanning option chains…"
            if scan_pending else "Live unavailable · no positive CE quote / scan pending"
        )
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

    # Apply post-verification routing before rendering Streamlit's radio
    # widget. Never modify a widget-owned session key after it exists.
    if st.session_state.pop("fno_activate_auto_quote_on_next_run", False):
        st.session_state["fno_price_provider"] = AUTO_SOURCE

    # Compact one-row toolbar: visible expiry + three tucked-away controls.
    # All prior Streamlit widget keys are preserved for saved user selections.
    expiry_col, search_col, filters_col, source_col, autofit_col = st.columns(
        [1.05, 0.9, 1.2, 0.85, 1.05], gap="small",
    )
    with expiry_col:
        from dhan_visual.otm_home import screen_expiry_changed
        expiry = st.selectbox(
            "F&O expiry month", expiries,
            index=expiries.index(choose_expiry(entries)),
            key="fno_expiry", label_visibility="collapsed",
            on_change=screen_expiry_changed,
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
                "Quote source", [AUTO_SOURCE, "Dhan", "Yahoo Finance"],
                index=0, key="fno_price_provider", horizontal=False,
                help="Default: verified Dhan LTP, with Yahoo only for missing "
                     "stocks. Yahoo alone until Dhan setup succeeds.",
            )
            manual = st.button("↻ Refresh prices", key="fno_manual_price_refresh")
            if st.button("↻ Refresh stock list", key="fno_refresh_universe"):
                _start_universe(force=True)
                st.rerun()
            # Source details are available in the tooltip; not repeated
            # as permanent instructional text on every app rerun.
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
    # No credential forms on Page 2. They live exclusively in Settings.
    # Home and Page 2 use one saved token and one shared quote snapshot.
    dhan_credentials = _local_creds()
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
    # Across browser/app restarts a verified Dhan connection defaults to
    # Live CE. A user-selected EOD preference still survives normal reruns.
    if ("fno_ce5_mode" not in st.session_state
            and verified_account(dhan_credentials[0]) is not None):
        st.session_state["fno_ce5_mode"] = "Live"
    if st.session_state.pop("fno_activate_live_on_next_run", False):
        # Safe to update before the CE radio widget exists in this run.
        st.session_state["fno_ce5_mode"] = "Live"
    eod_calls, eod_date = _free_ce5_panel(
        expiry, dhan_count=len(dhan_ce5), available=available,
    )
    ce_mode = st.session_state.get("fno_ce5_mode", "EOD")
    selected_ce = eod_calls if ce_mode == "EOD" else dhan_ce5
    # The lightweight first Home page and the full screener show the
    # same source+expiry CE data. Do NOT fetch a second option-chain.
    st.session_state["fno_shared_ce"] = {
        "month": expiry, "mode": ce_mode, "calls": selected_ce,
        "asof": eod_date,
    }
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
        scan_pending = bool(st.session_state.get("fno_straddle_job"))
        ce5_selected = [
            _ce5_choice(
                symbol, dhan_ce5, eod_calls, eod_date, ce_mode,
                scan_pending=scan_pending,
            )
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
    if rows:
        # Export fully computed cached levels without waiting for visual
        # stage animation; no extra Yahoo requests are made.
        exported = attach_technical_columns(
            pd.DataFrame(rows).drop(columns=["Company"], errors="ignore"),
            technicals, stage=4,
        )
        export_selected = [
            _ce5_choice(
                sym, dhan_ce5, eod_calls, eod_date, ce_mode,
                scan_pending=bool(st.session_state.get("fno_straddle_job")),
            )
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


def render_dhan_settings():
    """All broker credentials and one-stock options onboarding in Settings.

    No duplicate login controls on Home or the full Market Screener.
    The stock list and quotes are already prepared by those tabs, even while
    Settings is the visible tab. No order/trade methods.
    """
    st.markdown("### Settings · Dhan connection")
    st.caption(
        "Enter credentials on this PC. Connection and funds are verified "
        "read-only; option-chain verification shares prices with Home."
    )
    cid = str(st.session_state.get("dhan_local_cid") or read_client_id()).strip()
    need_market = bool(
        st.session_state.get("fno_ce5_mode") == "Live"
        or st.session_state.get("fno_price_provider") == "Dhan"
        or (cid and verified_account(cid) is not None)
    )
    _dhan_credentials(need_market=need_market)
    loaded = st.session_state.get("fno_universe_data")
    if loaded and loaded[0]:
        universe = loaded[0]
        entries = [item.entry for item in universe]
        expiries = expiry_options(entries)
        expiry = st.session_state.get("fno_home_expiry") or st.session_state.get("fno_expiry")
        if expiry not in expiries:
            expiry = choose_expiry(entries)
        available = [item for item in universe if expiry in item.entry.lots]
        st.divider()
        st.markdown("#### Options · one-stock verification")
        render_atm_panel(available, [], expiry)
    else:
        st.caption("The F&O instrument list is being prepared in the background.")
