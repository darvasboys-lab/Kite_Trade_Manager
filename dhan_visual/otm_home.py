"""OTM CE-first landing screen; all Dhan data stays read-only.

The full screener is rendered on Page 2 by Streamlit tabs, so its existing
EMA/R1, Yahoo, Dhan cash and market-list workers continue in the background.
Both pages use the SAME option-chain queue/snapshot, no duplicate CE calls.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

import pandas as pd
import streamlit as st

from .ce_eod_remote import IST
from .dhan_integration_state import (
    verified_account, token_still_current,
)
from .auto_auth import is_configured
from .dhan_readonly_check import check_dhan_data_plan, DhanProfileError
from .local_client_settings import valid_client_id
from .option_panel import _local_creds
from .quote_routing import (
    AUTO_SOURCE, choose_cash_source, merge_dhan_yahoo,
    nse_regular_session,
)
from .screener import choose_expiry, expiry_options
from .straddle_panel import (
    _scan_key, _drain_events, available_otm_calls, prepare_otm_scan,
)
from .table_layout import dataframe_key, autofit_column_config, autofit_enabled
from .screener_ui import (
    _auto_universe, _dhan_funds_panel, _start_dhan_prices,
    _start_yahoo_prices, _universe_key,
    _confirmed_dhan_profile,
)


def _home_expiry_changed() -> None:
    st.session_state["fno_expiry"] = st.session_state["fno_home_expiry"]


def screen_expiry_changed() -> None:
    """Page 2 expiry switch propagates back to the first Home page."""
    st.session_state["fno_home_expiry"] = st.session_state["fno_expiry"]


def _cash_quotes(universe, cid: str, token: str):
    """Reuse only account-matched cash quotes, with Yahoo per missing stock."""
    symbols = {item.entry.symbol for item in universe}
    state = st.session_state.get("fno_dhan_snapshot") or {}
    identity = sha256(
        (cid + ":" + token + ":" + _universe_key(universe)).encode()
    ).hexdigest() if valid_client_id(cid) and token else None
    dhan = (
        {sym: q for sym, q in state.get("quotes", {}).items() if sym in symbols}
        if state.get("key") == identity else {}
    )
    yahoo_snap = st.session_state.get("fno_yahoo_snapshot") or {}
    yahoo = {
        sym: q for sym, q in yahoo_snap.get("quotes", {}).items()
        if sym in symbols and getattr(q, "source", "").lower().startswith("yahoo")
    }
    return merge_dhan_yahoo(dhan, yahoo)


def otm_home_dataframe(available, cash_quotes, calls, *,
                       eod=False) -> pd.DataFrame:
    """Exactly three columns; missing option quotes are NaN, never fabricated.

    The price shown is the CASH CMP source; the CE % uses the option-chain's
    OWN underlying price from its same Dhan response (or NSE EOD own close).
    """
    items = []
    for entry in available:
        symbol = entry.entry.symbol
        option = calls.get(symbol)
        quote = cash_quotes.get(symbol)
        if option is None:
            ce_pct = None
        else:
            ce_pct = float(option.premium_pct)
        cash = float(quote.price) if quote is not None else None
        items.append({
            "Stock": symbol,
            "CMP (₹)": cash,
            "5%+ OTM CE (%)": ce_pct,
        })
    return pd.DataFrame(
        items, columns=["Stock", "CMP (₹)", "5%+ OTM CE (%)"]
    )


@st.fragment(run_every="3s")
def _otm_table(available, expiry, *, cid, token, cash_quotes):
    key = _scan_key(available, expiry)
    state = (st.session_state.get("fno_straddle_snapshots") or {}).get(key)
    if (state and state.get("client_id") == cid
            and _drain_events(state)):
        st.rerun(scope="app")
    calls = available_otm_calls(available, expiry)
    mode = st.session_state.get("fno_ce5_mode", "EOD")
    if mode == "EOD":
        shared = st.session_state.get("fno_shared_ce") or {}
        selected = (
            shared.get("calls", {})
            if shared.get("month") == expiry and shared.get("mode") == "EOD"
            else {}
        )
    else:
        selected = calls
    frame = otm_home_dataframe(available, cash_quotes, selected)
    total, priced = len(frame), int(frame["5%+ OTM CE (%)"].notna().sum())
    attempted = int(state.get("processed", 0)) if state else 0
    if mode == "Live":
        st.caption(
            f"Dhan option chain · {priced}/{total} CE premiums · "
            f"{attempted} stocks attempted · "
            f"{'scanning' if st.session_state.get('fno_straddle_job') else 'snapshot'}"
        )
    else:
        st.caption(
            f"NSE EOD · {priced}/{total} CE references · "
            "switch to Live for Dhan option-chain prices"
        )
    st.dataframe(
        frame, hide_index=True, use_container_width=True, height=625,
        key=dataframe_key("ccs_otm_ce_home", st.session_state),
        column_config=autofit_column_config({
            "Stock": st.column_config.TextColumn("Stock", width="medium"),
            "CMP (₹)": st.column_config.NumberColumn(
                "CMP (₹)", format="₹%.2f", width="medium",
            ),
            "5%+ OTM CE (%)": st.column_config.NumberColumn(
                "CE ≥ +5% (%)", format="%.2f%%", width="medium",
                help="First genuine listed CE strike at or above 105% of "
                     "the Dhan option-chain underlying price. Missing data "
                     "is blank, not a zero or synthetic option premium.",
            ),
        }, autofit_enabled(st.session_state)),
    )
    if mode == "Live" and (not priced or (state and state.get("errors"))):
        with st.expander("Why is an OTM CE value missing?", expanded=False):
            if state:
                if state.get("fatal"):
                    st.error(state["fatal"])
                if state.get("errors"):
                    for symbol, reason in list(state["errors"].items())[:6]:
                        st.caption(f"{symbol}: {reason}")
                if state.get("started"):
                    st.caption(
                        f"{attempted}/{total} option chains checked; "
                        "an unavailable first 5%+ CE LTP remains blank."
                    )
            else:
                st.caption(
                    "Dhan cash CMP alone cannot supply an option premium. "
                    "Check the connection, then start the paced scan."
                )
            st.caption(
                "A full-universe scan is sequential and takes time. "
                "HTTP 401/403/429 stop the scan; a Yahoo cash quote "
                "never becomes a Dhan CE option price."
            )


def render_otm_home():
    """Display priority OTM CE results and trigger one shared scan."""
    st.markdown("### OTM Covered Call · 5%+ Call Premium")
    universe = _auto_universe()
    if not universe:
        return
    entries = [item.entry for item in universe]
    expiries = expiry_options(entries)
    if not expiries:
        st.warning("No valid F&O expiry month available in the stock list")
        return

    if st.session_state.pop("fno_activate_auto_quote_on_next_run", False):
        st.session_state["fno_price_provider"] = AUTO_SOURCE
    if st.session_state.pop("fno_activate_live_on_next_run", False):
        st.session_state["fno_ce5_mode"] = "Live"
    selected = st.session_state.get("fno_home_expiry")
    if selected not in expiries:
        main_selected = st.session_state.get("fno_expiry")
        st.session_state["fno_home_expiry"] = (
            main_selected if main_selected in expiries else choose_expiry(entries)
        )
    with st.container():
        expiry_col, mode_col, check_col = st.columns([1.2, 1.2, 1.8])
        with expiry_col:
            expiry = st.selectbox(
                "Option month", expiries, key="fno_home_expiry",
                on_change=_home_expiry_changed,
            )
        with mode_col:
            st.caption("CE source: " + st.session_state.get("fno_ce5_mode", "EOD"))
        with check_col:
            st.caption("Read-only · no trading orders")

    available = [item for item in universe if expiry in item.entry.lots]
    cid, token = _local_creds()
    saved = verified_account(cid)
    profile = _confirmed_dhan_profile(cid, token)
    if "fno_ce5_mode" not in st.session_state:
        st.session_state["fno_ce5_mode"] = "Live" if (profile or saved) else "EOD"
    mode = st.session_state["fno_ce5_mode"]
    funds = st.session_state.get("fno_dhan_funds_snapshot") or {}
    balance_known = (
        bool(funds.get("data"))
        and funds.get("key") == sha256(
            (cid + ":" + token).encode()
        ).hexdigest()
        if valid_client_id(cid) and token else False
    )
    if profile:
        if profile.data_ready:
            st.success("✓ Dhan connected · Data API active · token verified")
        else:
            st.warning(
                "Dhan profile connected, but the Data API plan is inactive. "
                "OTM option-chain access is not available."
            )
    elif saved and token and token_still_current(saved):
        st.info(
            "Dhan setup saved · token present · live connection not "
            "rechecked in this session"
        )
    elif token:
        st.warning("Dhan access token available, but current session's "
                   "profile check is pending · Settings shows login status")
    elif valid_client_id(cid) and is_configured(cid):
        if st.session_state.get("dhan_auto_error"):
            st.warning("Saved Dhan PIN/TOTP login needs attention · "
                       "open Settings to see the broker response")
        else:
            st.info("Saved Dhan PIN/TOTP detected · generating or "
                    "recovering today's token automatically")
    else:
        st.warning("Dhan not connected · save PIN + permanent TOTP in Settings")

    if valid_client_id(cid) and len(token) >= 16:
        if st.button(
            "Check Dhan connection", key="otm_home_check_connection",
            help="One read-only Dhan Profile GET. Confirms current token "
                 "and Data API entitlement, without repeating setup.",
        ):
            try:
                result = check_dhan_data_plan(cid, token)
                st.session_state["fno_dhan_profile_status"] = {
                    "cid": cid, "status": result,
                    "token_fingerprint": sha256(token.encode()).hexdigest(),
                }
                if result.data_ready:
                    st.toast("✓ Dhan account and Data API verified", icon="✅")
                else:
                    st.warning(
                        "Dhan connected, but Data API subscription is inactive."
                    )
                st.rerun()
            except DhanProfileError as exc:
                st.error(str(exc))

    # Compact account balance from the same read-only funds endpoint, no
    # duplicate broker request between Home and Page 2.
    if profile or saved:
        _dhan_funds_panel(cid, token)

    # Start cash data first in the background; the full heavy screener will
    # consume the same snapshots on Page 2, without additional requests.
    provider = st.session_state.get("fno_price_provider", AUTO_SOURCE)
    selected_source = choose_cash_source(
        provider,
        verified=bool(saved and token and token_still_current(saved)),
        token_available=bool(token),
    )
    if selected_source == "Dhan":
        if valid_client_id(cid) and token:
            _start_dhan_prices(universe, cid, token)
        # A user explicitly selected Dhan: never quietly switch to Yahoo.
        # The full Page 2 will display broker-credential diagnostics.
    else:
        _start_yahoo_prices(universe, expiry)

    # OTM CE has priority over Page 2's EMA/R1 preparation. Start the
    # identical Dhan worker even with profile-only onboarding completed.
    if mode == "Live":
        status = prepare_otm_scan(available, expiry)
        col_a, col_b = st.columns([1.5, 2.5])
        with col_a:
            if st.button(
                "↻ Fetch 5%+ CE now", key="otm_home_fetch",
                disabled=(
                    not token or (
                        profile is not None and not profile.data_ready
                    ) or not (profile or saved)
                ),
                help="Uses one paced Dhan option-chain request per stock. "
                     "No broker orders or accelerated retries.",
                use_container_width=True,
            ):
                state = (st.session_state.get("fno_straddle_snapshots") or {}).get(
                    _scan_key(available, expiry)
                ) or {}
                if state.get("fatal") and (
                    "429" in str(state["fatal"]) or
                    "rate limit" in str(state["fatal"]).lower()
                ):
                    st.error(
                        "Dhan rate limit was reached. Do not retry "
                        "immediately; try again later."
                    )
                else:
                    status = prepare_otm_scan(available, expiry, explicit=True)
                    st.toast(status, icon="📈")
                    st.rerun()
        with col_b:
            if status and status not in ("Existing CE snapshot",):
                st.caption(status)
    else:
        st.caption("EOD selected · choose Live on Page 2 for Dhan CE")

    cash = _cash_quotes(universe, cid, token)
    _otm_table(available, expiry, cid=cid, token=token, cash_quotes=cash)
