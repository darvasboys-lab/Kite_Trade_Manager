"""OTM CE-first landing screen; all Dhan data stays read-only.

The full screener is rendered on Page 2 by Streamlit tabs, so its existing
EMA/R1, Yahoo, Dhan cash and market-list workers continue in the background.
Both pages use the SAME option-chain queue/snapshot, no duplicate CE calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pandas as pd
import streamlit as st

from .ce_eod_remote import IST
from .fyers_ui import connected_fyers, fyers_cash_prices, fyers_chain_calls
from .broker_control import active_broker, broker_allowed
from .ce_eod_store import load_local_eod, save_verified_report, eod_cash_quotes
from .nse_eod_ce import NseEodError, NseEodSnapshot, parse_nse_fo_eod
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
    _confirmed_dhan_profile, _parse_ce5_upload, _eod_start,
)


def _home_expiry_changed() -> None:
    st.session_state["fno_expiry"] = st.session_state["fno_home_expiry"]


def screen_expiry_changed() -> None:
    """Page 2 expiry switch propagates back to the first Home page."""
    st.session_state["fno_home_expiry"] = st.session_state["fno_expiry"]


def home_ce_mode_changed() -> None:
    """Keep Free EOD / Dhan Live mode shared with full Page 2."""
    st.session_state["fno_ce5_mode"] = st.session_state["fno_home_ce_source"]


def page_two_ce_mode_changed() -> None:
    st.session_state["fno_home_ce_source"] = st.session_state["fno_ce5_mode"]


def choose_home_eod_calls(
    expiry: str, shared: dict, uploaded: dict,
) -> tuple[dict, str]:
    """Newest validated EOD snapshot wins; nothing is passed off as live."""
    sources = []
    if (isinstance(shared, dict) and shared.get("month") == expiry
            and shared.get("mode") == "EOD" and shared.get("asof")):
        sources.append((str(shared["asof"]), shared.get("calls") or {}))
    if isinstance(uploaded, dict):
        snap = uploaded.get("snapshot")
        if isinstance(snap, NseEodSnapshot):
            sources.append((snap.report_date, snap.quotes.get(expiry, {})))
    if not sources:
        return {}, ""
    day, values = max(sources, key=lambda row: row[0])
    return values, day


def validate_home_eod_upload(payload: bytes, *,
                             today: date | None = None) -> NseEodSnapshot:
    """Reject stale/future and invalid owner-imported NSE reports."""
    snap = _parse_ce5_upload(payload)
    day = date.fromisoformat(snap.report_date)
    reference = today or datetime.now(IST).date()
    if day > reference:
        raise NseEodError("NSE report has a future business date")
    if reference - day > timedelta(days=10):
        raise NseEodError(
            "NSE report is older than 10 days. Please download a recent report."
        )
    return snap


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
                       eod=False, straddles=None, expiry=None) -> pd.DataFrame:
    """Four-column Home table; never invent an absent option-chain premium.

    CE % is calculated by the broker parser from the SAME option-chain
    underlying. The ATM total (₹ per share, CE LTP + PE LTP) is shown only
    when BOTH real ATM legs have the same symbol and selected expiry.
    No cross-provider fallback is permitted for the options columns.
    """
    items = []
    straddles = straddles or {}
    for entry in available:
        symbol = entry.entry.symbol
        option = calls.get(symbol)
        quote = cash_quotes.get(symbol)
        ce_pct = float(option.premium_pct) if option is not None else None
        cash = float(quote.price) if quote is not None else None
        atm = straddles.get(symbol)
        total = None
        if (expiry is not None and atm is not None
                and getattr(atm, "symbol", None) == symbol
                and getattr(atm, "expiry", None) == expiry):
            ce, pe = getattr(atm, "call_ltp", None), getattr(atm, "put_ltp", None)
            if ce is not None and pe is not None and ce > 0 and pe > 0:
                total = float(ce + pe)
        items.append({
            "Stock": symbol,
            "CMP (₹)": cash,
            "5%+ OTM CE (%)": ce_pct,
            "ATM Straddle Total (₹)": total,
        })
    return pd.DataFrame(
        items, columns=[
            "Stock", "CMP (₹)", "5%+ OTM CE (%)",
            "ATM Straddle Total (₹)",
        ]
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
    eod_date = ""
    if mode == "EOD":
        selected, eod_date = choose_home_eod_calls(
            expiry,
            st.session_state.get("fno_shared_ce") or {},
            st.session_state.get("fno_ce5_home_uploaded") or {},
        )
    elif mode == "FYERS":
        fyers_snap = st.session_state.get("fno_fyers_chain_snapshot") or {}
        # A snapshot for a prior expiry must never appear as this month's CE
        # or ATM straddle. Each row uses the existing single FYERS chain scan.
        if fyers_snap.get("expiry") == expiry and broker_allowed("FYERS"):
            selected = fyers_snap.get("calls") or {}
            straddles = fyers_snap.get("straddles") or {}
        else:
            selected, straddles = {}, {}
    else:
        selected = calls
    if mode == "Live":
        straddles = (
            state.get("rows") or {} if state and state.get("client_id") == cid
            and broker_allowed("Dhan") else {}
        )
    elif mode == "EOD":
        straddles = {}
    frame = otm_home_dataframe(
        available, cash_quotes, selected, straddles=straddles, expiry=expiry,
    )
    total, priced = len(frame), int(frame["5%+ OTM CE (%)"].notna().sum())
    straddle_count = int(frame["ATM Straddle Total (₹)"].notna().sum())
    attempted = int(state.get("processed", 0)) if state else 0
    if mode == "Live":
        st.caption(
            f"Dhan option chain · {priced}/{total} CE premiums · "
            f"{attempted} stocks attempted · "
            f"{'scanning' if st.session_state.get('fno_straddle_job') else 'snapshot'}"
        )
    elif mode == "FYERS":
        st.caption(
            f"FYERS option chain · {priced}/{total} genuine +5% CE LTP · "
            f"{straddle_count}/{total} ATM CE+PE totals · read-only"
        )
    else:
        st.caption(
            f"Free NSE EOD {eod_date or '(report not loaded)'} · "
            f"{priced}/{total} CE closing-price references · not live"
        )
    st.dataframe(
        frame, hide_index=True, use_container_width=True, height=625,
        key=dataframe_key("ccs_otm_ce_home", st.session_state),
        column_config=autofit_column_config({
            "Stock": st.column_config.TextColumn("Stock", width="medium"),
            "CMP (₹)": st.column_config.NumberColumn(
                "CMP (₹)", format="₹%.2f", width="medium",
                help="Cash quote where available; otherwise a dated NSE "
                     "F&O underlying close, NOT a live CMP.",
            ),
            "5%+ OTM CE (%)": st.column_config.NumberColumn(
                "CE ≥ +5% (%)", format="%.2f%%", width="medium",
                help="First genuine listed CE strike at or above 105% of "
                     "the same-source underlying spot price. Missing data "
                     "is blank, not a zero or synthetic option premium.",
            ),
            "ATM Straddle Total (₹)": st.column_config.NumberColumn(
                "ATM Straddle Total (₹)", format="₹%.2f", width="medium",
                help="Total premium per share at the nearest listed ATM "
                     "strike: actual CE LTP + PE LTP for the SAME stock, "
                     "strike, expiry and broker option-chain response. "
                     "Blank until both positive prices are available. "
                     "Not per-lot profit or guaranteed executable credit.",
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

    if st.session_state.pop("fno_activate_auto_quote_on_next_run", False) and broker_allowed("Dhan"):
        st.session_state["fno_price_provider"] = AUTO_SOURCE
    if st.session_state.pop("fno_fyers_select_on_next_run", False) and broker_allowed("FYERS"):
        st.session_state["fno_price_provider"] = "FYERS"
        st.session_state["fno_ce5_mode"] = "FYERS"
        st.session_state["fno_home_ce_source"] = "FYERS"
    if st.session_state.pop("fno_fyers_disconnect_next_run", False):
        st.session_state["fno_price_provider"] = AUTO_SOURCE
        st.session_state["fno_ce5_mode"] = "EOD"
        st.session_state["fno_home_ce_source"] = "EOD"
    pending_live = st.session_state.pop("fno_activate_live_on_next_run", False)
    # Respect an ACTIVE Data API subscription only; a working Dhan login
    # without the data plan cannot supply genuine CE option LTP.
    cid, token = _local_creds() if broker_allowed("Dhan") else ("", "")
    saved = verified_account(cid) if broker_allowed("Dhan") else None
    profile = _confirmed_dhan_profile(cid, token)
    inactive = bool(profile is not None and not profile.data_ready)
    if "fno_ce5_mode" not in st.session_state:
        st.session_state["fno_ce5_mode"] = (
            "Live" if (profile is not None and profile.data_ready) or
            (saved is not None and profile is None and token and
             token_still_current(saved)) else "EOD"
        )
    if pending_live and not inactive and broker_allowed("Dhan"):
        st.session_state["fno_ce5_mode"] = "Live"
    if inactive and st.session_state.get("fno_ce5_mode") == "Live":
        # Inactive Dhan data cannot disable independent FYERS data.
        st.session_state["fno_ce5_mode"] = "EOD"
    current_mode = st.session_state["fno_ce5_mode"]
    if ("fno_home_ce_source" not in st.session_state
            or st.session_state["fno_home_ce_source"] != current_mode):
        st.session_state["fno_home_ce_source"] = current_mode
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
            mode_choices = (
                ["EOD", "FYERS"] if active_broker() == "FYERS"
                else ["EOD", "Live"] if active_broker() == "Dhan"
                else ["EOD"]
            )
            st.radio(
                "CE source", mode_choices,
                key="fno_home_ce_source", horizontal=True,
                on_change=home_ce_mode_changed,
            )
        with check_col:
            st.caption("Read-only · no trading orders")

    available = [item for item in universe if expiry in item.entry.lots]
    mode = st.session_state["fno_ce5_mode"]
    funds = st.session_state.get("fno_dhan_funds_snapshot") or {}
    balance_known = (
        bool(funds.get("data"))
        and funds.get("key") == sha256(
            (cid + ":" + token).encode()
        ).hexdigest()
        if valid_client_id(cid) and token else False
    )
    if broker_allowed("Dhan"):
        if profile:
            if profile.data_ready:
                st.success("✓ Dhan connected · Data API active · token verified")
            else:
                st.caption(
                    "Dhan login connected · paid Data API inactive · "
                    "free NSE EOD CE mode selected"
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

    elif active_broker() == "FYERS":
        st.caption("FYERS selected · Dhan login, verification and funds polling paused.")
    else:
        st.caption("Public only · no broker account requests.")

    # Start cash data first in the background; the full heavy screener will
    # consume the same snapshots on Page 2, without additional requests.
    provider = st.session_state.get("fno_price_provider", AUTO_SOURCE)
    selected_source = choose_cash_source(
        provider,
        verified=bool(saved and token and token_still_current(saved)),
        token_available=bool(token),
    )
    if provider == "FYERS":
        fyers_cash_prices([item.entry.symbol for item in universe])
    elif selected_source == "Dhan" and inactive:
        # Profile and funds GET remain available even when market quotes are
        # blocked. Do not repeatedly call paid-only quote endpoints.
        if provider == AUTO_SOURCE:
            _start_yahoo_prices(universe, expiry)
        else:
            st.caption("Dhan market-data plan inactive · using NSE EOD "
                       "reference prices when a daily report is available.")
    elif selected_source == "Dhan":
        if broker_allowed("Dhan") and valid_client_id(cid) and token:
            _start_dhan_prices(universe, cid, token)
    else:
        _start_yahoo_prices(universe, expiry)

    # OTM CE has priority over Page 2's EMA/R1 preparation. Start the
    # identical Dhan worker even with profile-only onboarding completed.
    if mode == "FYERS":
        if connected_fyers():
            fyers_chain_calls(available, expiry)
        else:
            st.info("Open Settings → FYERS to authorize your "
                    "non-trading account and load genuine CE/PE prices.")
    elif mode == "Live" and broker_allowed("Dhan"):
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
        # Check the official exchange report automatically. Only a bounded
        # existing published report is requested; account APIs are untouched.
        _eod_start()
        imported = st.session_state.get("fno_ce5_home_uploaded") or {}
        snap = imported.get("snapshot")
        local_snap, local_label = load_local_eod()
        if (local_snap is not None and
                (not isinstance(snap, NseEodSnapshot)
                 or local_snap.report_date > snap.report_date)):
            snap = local_snap
            st.session_state["fno_ce5_home_uploaded"] = {
                "snapshot": snap, "filename": local_label,
            }
        remote = st.session_state.get("fno_ce5_remote_snapshot")
        job = st.session_state.get("fno_ce5_remote_job")
        downloading = bool(job and not job["future"].done())
        if isinstance(snap, NseEodSnapshot):
            count = len(snap.quotes.get(expiry, {}))
            st.success(
                f"✓ NSE EOD {snap.report_date} loaded automatically/from local cache · "
                f"{count} eligible 5%+ CE reference premiums for {expiry}"
            )
            st.caption(
                "Historical NSE close, not live LTP · "
                "a newer report is checked automatically when available."
            )
        elif downloading:
            st.info(
                "Checking the official NSE F&O daily report automatically. "
                "No daily ZIP upload required when NSE permits download."
            )
        elif remote and remote.snapshot is None:
            st.warning(
                "Automatic NSE report download did not succeed: "
                f"{remote.message} The data will be retried on the next "
                "report session or when you choose Refresh. A manual file "
                "remains an optional fallback."
            )
        else:
            st.info(
                "Free NSE EOD auto-fetch is enabled. Checking the latest "
                "published daily report; no Dhan subscription required."
            )
        if remote and remote.snapshot is None and isinstance(snap, NseEodSnapshot):
            st.caption(
                f"Latest automatic check: {remote.message} "
                "Showing the last successfully saved report instead."
            )
        if st.session_state.get("fno_ce5_auto_save_error"):
            st.warning(st.session_state["fno_ce5_auto_save_error"])
        if st.button(
            "↻ Refresh NSE EOD now",
            key="fno_home_eod_refresh",
            disabled=downloading,
            help="One bounded official NSE archive check, without Dhan "
                 "market-data calls or creating a new trading order.",
        ):
            st.session_state.pop("fno_ce5_remote_attempt_key", None)
            _eod_start()
            st.rerun()
        with st.expander(
            "Optional manual ZIP import (only if automatic access is blocked)",
            expanded=False,
        ):
            st.markdown(
                "No daily upload is normally needed: the app checks NSE "
                "automatically and remembers the last valid report. If "
                "NSE denies automated downloads (403/429), you can "
                "optionally get **F&O-UDiFF Common Bhavcopy Final (ZIP)** "
                "from [NSE Derivatives Reports]"
                "(https://www.nseindia.com/all-reports-derivatives) "
                "and import it once for that trading date."
            )
            uploaded = st.file_uploader(
                "NSE F&O daily ZIP or CSV (free)",
                type=["zip", "csv"],
                key="fno_home_eod_file",
            )
            if uploaded is not None:
                try:
                    raw_report = uploaded.getvalue()
                    snapshot = validate_home_eod_upload(raw_report)
                    # The last valid NSE report is available after app restart,
                    # without saving any Dhan credentials or calling its API.
                    saved_snapshot = save_verified_report(raw_report)
                    if saved_snapshot.report_date > snapshot.report_date:
                        snapshot = saved_snapshot
                    snap = snapshot
                    st.session_state["fno_ce5_home_uploaded"] = {
                        "snapshot": snapshot, "filename": uploaded.name,
                    }
                    st.success(
                        f"✓ Read NSE {snapshot.report_date} · "
                        f"{len(snapshot.quotes.get(expiry, {}))} "
                        f"matching {expiry} stock calls"
                    )
                    if not snapshot.quotes.get(expiry):
                        st.warning(
                            "This file is valid but contains no eligible "
                            f"stock CE closing prices for {expiry}. "
                            "Try an expiry month that exists in the report."
                        )
                except (NseEodError, ValueError, OSError) as exc:
                    st.error(f"Cannot use the selected NSE file: {exc}")
            st.caption(
                "Reports contain closing prices and may include "
                "untraded contracts. These are references, not executable "
                "premiums or a real-time feed."
            )

    cash = _cash_quotes(universe, cid, token) if broker_allowed("Dhan") else {}
    if provider == "FYERS" and connected_fyers():
        # Do not mix prior Dhan snapshots into FYERS cash prices.
        cash = fyers_cash_prices([item.entry.symbol for item in universe])
    elif not broker_allowed("Dhan"):
        # Public-only mode uses the existing Yahoo cache, not a broker login.
        cash = _cash_quotes(universe, "", "")
    if mode == "EOD":
        # Historical spot references can fill a blank cash column, but must
        # never replace genuine available Dhan or Yahoo prices.
        snap_options = [
            candidate for candidate in (
                (st.session_state.get("fno_ce5_home_uploaded") or {}).get("snapshot"),
                st.session_state.get("fno_eod_selected_snapshot"),
            ) if isinstance(candidate, NseEodSnapshot)
        ]
        if snap_options:
            best = max(snap_options, key=lambda candidate: candidate.report_date)
            reference = eod_cash_quotes(best)
            fallback_count = sum(
                1 for sym in reference
                if sym not in cash and sym in {i.entry.symbol for i in available}
            )
            cash = merge_dhan_yahoo(reference, cash)
            if fallback_count:
                st.caption(
                    f"NSE F&O report {best.report_date} · {fallback_count} "
                    "historical closing references fill missing CMP cells. "
                    "Not live stock prices."
                )
    _otm_table(available, expiry, cid=cid, token=token, cash_quotes=cash)
