"""Collapsed, click-to-load ATM CE/PE widget for the stock F&O screener.

The widget makes NO network request before the explicit Load button is pressed.
Option-chain prices always come from Dhan, even when the user selects Yahoo as
the cash-price source. No order APIs and no alteration to the trading bots.
"""
from __future__ import annotations

import os

import streamlit as st

from .atm_options import OptionDataError, load_atm_options
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
    """Read only local session/environment/vault; never initiate auto-login."""
    cid = str(
        st.session_state.get("dhan_local_cid")
        or os.environ.get("DHAN_CLIENT_ID")
        or read_client_id()
        or ""
    ).strip()
    token = str(
        st.session_state.get("dhan_local_access_token")
        or os.environ.get("DHAN_ACCESS_TOKEN")
        or ""
    ).strip()
    auto = st.session_state.get("dhan_auto_access")
    if not token and isinstance(auto, AutoAccess) and auto.usable():
        token = auto.token
    if not token and cid and is_configured(cid):
        old = read_cached_token(cid)
        if old:
            token = old.token
    return cid, token


def render_atm_panel(available, filtered_rows, expiry):
    """Shows ATM CE/PE only for the chosen underlying, after button click."""
    with st.expander("ATM options · CE / PE (load when needed)", expanded=False):
        st.caption(
            "Select a stock to retrieve its ATM call and put premiums, "
            "bid/ask prices and premium value per lot. This uses Dhan's read-only "
            "option chain and makes no request until Load is clicked."
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
        pressed = st.button(
            "Load ATM CE / PE",
            key="fno_atm_load",
            help="Fetch the selected stock's active expiry and its option chain from Dhan.",
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
                            st.session_state["fno_atm_result"] = {
                                "symbol": selected, "month": expiry, "quote": result,
                            }
                        except OptionDataError as exc:
                            st.error(str(exc))
        saved = st.session_state.get("fno_atm_result") or {}
        if saved.get("symbol") != selected or saved.get("month") != expiry:
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
        st.caption(
            f"Dhan option-chain snapshot requested {result.captured_at}. "
            "Values are indicative and can be stale outside trading hours; "
            "premium × lot is not expected profit or required margin."
        )
