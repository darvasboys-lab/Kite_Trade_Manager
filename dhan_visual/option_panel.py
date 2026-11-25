"""Collapsed, click-to-load ATM CE/PE widget for the stock F&O screener.

The widget makes NO network request before the explicit Load button is pressed.
Option-chain prices always come from Dhan, even when the user selects Yahoo as
the cash-price source. No order APIs and no alteration to the trading bots.
"""
from __future__ import annotations

import os
from hashlib import sha256

import streamlit as st

from .atm_options import OptionDataError, load_atm_options
from .straddle_scan import otm_call_5_from_atm, straddle_from_atm
from .dhan_readonly_check import DhanProfileError, check_dhan_data_plan
from .dhan_integration_state import (
    verified_account, token_still_current, load_manual_token, record_verification,
    parse_dhan_token_expiry, save_manual_token,
)
from .auto_auth import AutoAccess, AutoAuthError, is_configured, load_or_generate, read_cached_token
from .local_client_settings import read_client_id, save_client_id, valid_client_id


def format_rupees(raw, compact=False) -> str:
    if raw is None:
        return "N/A"
    value = float(raw)
    if compact and abs(value) >= 100000:
        return f"₹{value / 100000:.1f}L"
    return f"₹{value:,.2f}"


def _local_creds():
    """Read-only local credentials, with verified token-expiry/account guards."""
    cid = str(
        st.session_state.get("dhan_local_cid")
        or os.environ.get("DHAN_CLIENT_ID")
        or read_client_id()
        or ""
    ).strip()
    manual = str(
        st.session_state.get("dhan_local_access_token")
        or os.environ.get("DHAN_ACCESS_TOKEN")
        or ""
    ).strip()
    # Never offer a stale manual token to option-chain workers once the
    # original verified Dhan Profile validity has passed.
    saved = verified_account(cid)
    if saved is not None and not token_still_current(saved):
        manual = ""
    auto = st.session_state.get("dhan_auto_access")
    auto_token = (
        auto.token
        if (isinstance(auto, AutoAccess) and auto.usable()
            and st.session_state.get("dhan_auto_access_cid") == cid)
        else ""
    )
    cached_auto = read_cached_token(cid) if cid and is_configured(cid) else None
    token = (
        manual or auto_token
        or (cached_auto.token if cached_auto else "")
        or (load_manual_token(cid) if cid else "")
    )
    return cid, token


def render_atm_panel(available, filtered_rows, expiry):
    """Explicit one-stock Dhan read-only chain verification before full scan."""
    cid_for_status, _ = _local_creds()
    existing_connection = verified_account(cid_for_status)
    with st.expander(
        "Dhan connection test · ATM straddle + 5% CE"
        if existing_connection is None
        else "Dhan connection · optional one-stock recheck",
        expanded=False,
    ):
        if existing_connection is not None:
            st.success(
                "✓ Integration already completed successfully. "
                "The Data API and one-stock Straddle/5% CE were checked. "
                "You do not have to run integration again."
            )
            if st.button(
                "Show optional verification controls",
                key="fno_dhan_optional_recheck",
            ):
                st.session_state["fno_dhan_show_recheck"] = (
                    not st.session_state.get("fno_dhan_show_recheck", False)
                )
            if not st.session_state.get("fno_dhan_show_recheck", False):
                return

        st.caption(
            "First verify ONE stock's exact selected-month expiry, ATM CE/PE "
            "and 5%+ OTM CE in a single Dhan option-chain response. "
            "Requires Data API entitlement. No market-data request is sent "
            "until you click Test. No orders or account changes."
        )
        items = {x.entry.symbol: x for x in available}
        symbols = sorted(items)
        if not symbols:
            st.info("No stock option contracts for this expiry month.")
            return
        first = filtered_rows[0]["Symbol"] if filtered_rows else symbols[0]
        selected = st.selectbox(
            "Option underlying",
            symbols,
            index=symbols.index(first if first in items else symbols[0]),
            key="fno_atm_symbol",
        )
        item = items[selected]
        cid, token = _local_creds()
        if not valid_client_id(cid):
            cid = st.text_input(
                "Dhan Client ID for option prices", key="fno_atm_client_id"
            ).strip()
        override_token = ""
        if not token:
            override_token = st.text_input(
                "Dhan access token (ATM options only)",
                type="password", key="fno_atm_access_token",
                help="Optional if you enabled the secure TOTP setup in the Dhan tab. "
                     "Enter credentials only on your own laptop.",
            )
        # The official GET /v2/profile API verifies subscription entitlement
        # without calling any order or account-mutating endpoint.
        if st.button(
            "1 · Check token and Data API plan", key="fno_dhan_profile_check",
            help="One read-only Dhan profile GET; confirms account, token and dataPlan.",
        ):
            st.session_state.pop("fno_dhan_profile_status", None)
            if not valid_client_id(cid):
                st.error("Enter your Dhan Client ID locally first.")
            elif not (token or override_token.strip()):
                st.error(
                    "Enter an existing Dhan access token, or use the "
                    "already-configured local TOTP option. "
                    "No new token will be generated by this profile check."
                )
            else:
                with st.spinner("Checking read-only Dhan account profile…"):
                    try:
                        used_token = token or override_token.strip()
                        profile = check_dhan_data_plan(cid, used_token)
                        st.session_state["fno_dhan_profile_status"] = {
                            "cid": cid, "status": profile,
                            "token_fingerprint": sha256(used_token.encode()).hexdigest(),
                        }
                    except DhanProfileError as exc:
                        st.error(str(exc))

        saved_profile = st.session_state.get("fno_dhan_profile_status") or {}
        if saved_profile.get("cid") == cid:
            profile = saved_profile["status"]
            if profile.data_ready:
                st.success(
                    "Dhan Data API plan ACTIVE · "
                    f"validity: {profile.data_validity}"
                )
            else:
                st.warning(
                    f"Dhan Data API plan: {profile.data_plan} · "
                    "option chains may require activating the Dhan Data API subscription."
                )
            st.caption(
                f"Token validity: {profile.token_validity} · "
                f"Derivative segment: "
                f"{'enabled' if profile.derivatives_enabled is True else 'not confirmed' if profile.derivatives_enabled is None else 'not listed'} · "
                "checked via read-only Dhan Profile API."
            )

        pressed = st.button(
            "2 · Test Dhan · ATM + straddle + 5% CE",
            key="fno_atm_load",
            help="One read-only Dhan chain request for exactly the selected stock. "
                 "Checks ATM CE, PE, straddle ratio and first 5%+ OTM call.",
        )
        if pressed:
            st.session_state.pop("fno_atm_result", None)
            if not valid_client_id(cid):
                st.error("Enter a valid Dhan Client ID to request option-chain data.")
            else:
                if read_client_id() != cid:
                    save_client_id(cid)
                access_token = token or override_token.strip()
                if not access_token and is_configured(cid):
                    with st.spinner("Retrieving token through your opt-in TOTP settings…"):
                        try:
                            # TOTP login is only possible after the owner enabled
                            # it previously, and only when they clicked Load.
                            access = load_or_generate(cid)
                            access_token = access.token
                            st.session_state["dhan_auto_access"] = access
                            st.session_state["dhan_auto_access_cid"] = cid
                        except AutoAuthError as exc:
                            st.error(f"Dhan option-chain authentication failed: {exc}")
                if len(access_token) < 16:
                    st.info(
                        "Dhan option-chain data requires a current market-data "
                        "token. Enter it here, or enable secure TOTP auto-login "
                        "under Dhan settings."
                    )
                else:
                    with st.spinner(f"Fetching {selected} ATM CE / PE from Dhan…"):
                        try:
                            result = load_atm_options(
                                selected, expiry, item.entry.lots[expiry],
                                item.equity_security_id, cid, access_token,
                            )
                            # Reject a provider/library mismatch before
                            # saving or displaying any broker-owned price.
                            if result.symbol != selected:
                                raise OptionDataError(
                                    "Dhan option-chain symbol does not match the selected stock"
                                )
                            st.session_state["fno_atm_result"] = {
                                "symbol": selected, "month": expiry,
                                "cid": cid, "quote": result,
                            }
                            # Mark onboarding complete ONLY after the profile
                            # verified an ACTIVE Data API and this same token
                            # retrieved BOTH ATM straddle and +5% OTM CE.
                            # This is never done on page load or a mock quote.
                            profile_entry = (
                                st.session_state.get("fno_dhan_profile_status") or {}
                            )
                            verified_profile = profile_entry.get("status")
                            profile_matches_token = (
                                profile_entry.get("cid") == cid
                                and profile_entry.get("token_fingerprint") ==
                                sha256(access_token.encode()).hexdigest()
                                and verified_profile is not None
                                and verified_profile.data_ready
                            )
                            try:
                                straddle_from_atm(result)
                                otm = otm_call_5_from_atm(result)
                            except OptionDataError:
                                otm = None
                            if profile_matches_token and otm is not None:
                                expiry_time = parse_dhan_token_expiry(
                                    verified_profile.token_validity
                                )
                                if expiry_time is None:
                                    st.warning(
                                        "Option data works, but Dhan token expiry "
                                        "could not be verified. Complete the "
                                        "profile check with a token showing validity."
                                    )
                                else:
                                    try:
                                        record_verification(
                                            cid, selected, result.expiry,
                                            verified_profile.token_validity,
                                        )
                                        if st.session_state.get(
                                            "dhan_remember_manual_token", False
                                        ) and not isinstance(
                                            st.session_state.get("dhan_auto_access"),
                                            AutoAccess,
                                        ):
                                            try:
                                                save_manual_token(
                                                    cid, access_token,
                                                    verified_profile.token_validity,
                                                )
                                            except AutoAuthError as exc:
                                                st.warning(
                                                    "Integration complete but current "
                                                    f"token was not saved: {exc}"
                                                )
                                        st.session_state["fno_dhan_show_recheck"] = False
                                        st.session_state["fno_dhan_integration_complete"] = True
                                        st.session_state["fno_activate_live_on_next_run"] = True
                                    except (ValueError, OSError) as exc:
                                        st.warning(
                                            "Live quote verified but connection status "
                                            f"could not be saved: {type(exc).__name__}"
                                        )
                            elif verified_profile is None:
                                st.info(
                                    "The option chain loaded. To finish one-time "
                                    "integration, click step 1 (Check Data API plan) "
                                    "and then step 2 with the same token."
                                )
                            elif not profile_matches_token:
                                st.warning(
                                    "Profile and options must be verified with "
                                    "the same current token to complete setup."
                                )
                            # Refresh the upstream CE table and green status
                            # using this SAME option-chain snapshot.
                            st.rerun()
                        except OptionDataError as exc:
                            st.error(str(exc))
        saved = st.session_state.get("fno_atm_result") or {}
        if (saved.get("symbol") != selected
                or saved.get("month") != expiry
                or (saved.get("cid") and saved["cid"] != cid)):
            return
        result = saved["quote"]
        compact = st.session_state.get("fno_compact_contract_value", True)
        st.caption(
            f"Expiry {result.expiry} · ATM strike {format_rupees(result.strike)} · "
            f"Underlying {format_rupees(result.underlying_price)}"
        )
        ce, pe = st.columns(2)
        with ce:
            st.metric("ATM CE · LTP/share", format_rupees(result.ce.ltp))
            st.caption(
                f"Bid {format_rupees(result.ce.bid)} · "
                f"Ask {format_rupees(result.ce.ask)}"
            )
            st.caption(
                f"CE premium × lot ({result.lot_size}): "
                f"**{format_rupees(result.ce.premium_per_lot(result.lot_size), compact)}**"
            )
        with pe:
            st.metric("ATM PE · LTP/share", format_rupees(result.pe.ltp))
            st.caption(
                f"Bid {format_rupees(result.pe.bid)} · "
                f"Ask {format_rupees(result.pe.ask)}"
            )
            st.caption(
                f"PE premium × lot ({result.lot_size}): "
                f"**{format_rupees(result.pe.premium_per_lot(result.lot_size), compact)}**"
            )
        # One broker request serves both user-required calculations. A
        # positive result proves token, entitlement, expiry and data parsing
        # for THIS stock only; it does not certify the full-universe scan.
        try:
            straddle = straddle_from_atm(result)
        except OptionDataError as exc:
            st.warning(f"ATM straddle not available: {exc}")
        else:
            st.metric(
                "ATM Straddle % (CE + PE / underlying)",
                f"{float(straddle.premium_pct):.2f}%",
            )
            st.caption(
                f"CE + PE LTP ₹{float(straddle.combined_ltp):,.2f} · "
                f"gross premium/lot ₹{float(straddle.gross_lot_premium):,.2f}. "
                "Not guaranteed sell credit or profit."
            )
        otm = otm_call_5_from_atm(result)
        if otm is None:
            st.warning(
                "5%+ OTM CE not available: no first eligible listed call "
                "with a positive Dhan LTP in this response. "
                "Do not substitute another strike."
            )
        else:
            st.metric(
                "5%+ OTM CE / underlying",
                f"{float(otm.premium_pct):.2f}%",
            )
            st.caption(
                f"First listed strike ≥ 105% of Dhan underlying: "
                f"₹{float(otm.strike):,.2f} · "
                f"CE LTP ₹{float(otm.call_ltp):,.2f}. "
                "The CE value can also appear in the main table in Live mode."
            )
        if result.ce.ltp is not None and result.pe.ltp is not None and otm:
            st.success(
                "Dhan read-only option chain verified for this stock: "
                "ATM CE, ATM PE, straddle and 5%+ OTM call."
            )
        st.caption(
            f"Dhan option-chain snapshot requested {result.captured_at}. "
            "This verifies one stock only; data can be stale after market hours. "
            "Premium × lot is not expected profit or required margin. "
            "Use the Live CE toggle to see Dhan calls in the main table."
        )
