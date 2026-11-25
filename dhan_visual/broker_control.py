"""One active broker connection at a time in the local Streamlit workspace.

Switching never deletes either broker's Windows vault credentials or revokes
existing server-side sessions. It stops new requests to the old broker, waits
for its already-submitted read-only jobs to finish, then changes the active
broker. Yahoo and NSE public EOD are not broker logins.
"""
from __future__ import annotations

import streamlit as st

ACTIVE_KEY = "ccs_active_broker"  # authoritative state, never a widget key
SELECTION_KEY = "ccs_broker_requested"  # radio widget value
PENDING_KEY = "ccs_pending_broker"
OPTIONS = ("Dhan", "FYERS", "Public only")
DHAN_JOB_KEYS = (
    "dhan_auto_auth_job", "dhan_auto_profile_job",
    "fno_dhan_job", "fno_dhan_funds_job", "fno_straddle_job",
)
FYERS_JOB_KEYS = ("fno_fyers_cash_job", "fno_fyers_chain_job")


def active_broker() -> str:
    chosen = st.session_state.get(ACTIVE_KEY, "Dhan")
    return chosen if chosen in OPTIONS else "Dhan"


def broker_allowed(name: str) -> bool:
    """Only the selected broker may initiate a new broker request."""
    return active_broker() == name and not st.session_state.get(PENDING_KEY)


def _is_busy(job) -> bool:
    return isinstance(job, dict) and bool(
        job.get("future") and not job["future"].done()
    )


def _busy_jobs(name: str) -> list[str]:
    keys = DHAN_JOB_KEYS if name == "Dhan" else FYERS_JOB_KEYS
    pending = [key for key in keys if _is_busy(st.session_state.get(key))]
    # FYERS daily indicators/R1 scans carry an access token in the worker.
    if name == "FYERS":
        for key in ("fno_technical_job", "ccs_r1_today_job"):
            if _is_busy(st.session_state.get(key)):
                pending.append(key)
    return pending


def _request_cancel(name: str) -> None:
    keys = (
        ("fno_straddle_job",)
        if name == "Dhan"
        else ("fno_fyers_chain_job", "fno_technical_job",
              "ccs_r1_today_job")
    )
    for key in keys:
        job = st.session_state.get(key)
        if isinstance(job, dict):
            control = job.get("cancel") or job.get("stop")
            if control is not None:
                control.set()


def _apply_source_selection(target: str) -> None:
    """Align both option selectors and cash provider before widget creation."""
    if target == "FYERS":
        st.session_state["fno_price_provider"] = "FYERS"
        st.session_state["fno_ce5_mode"] = "FYERS"
        st.session_state["fno_home_ce_source"] = "FYERS"
    elif target == "Dhan":
        # Reuse saved Dhan token on demand; don't force paid options data.
        st.session_state["fno_price_provider"] = "Auto (Dhan → Yahoo)"
        st.session_state["fno_ce5_mode"] = "EOD"
        st.session_state["fno_home_ce_source"] = "EOD"
    else:
        st.session_state["fno_price_provider"] = "Yahoo Finance"
        st.session_state["fno_ce5_mode"] = "EOD"
        st.session_state["fno_home_ce_source"] = "EOD"


def _switch_callback() -> None:
    """Runs before script widgets are re-created, so keys can be updated."""
    target = st.session_state.get(SELECTION_KEY, active_broker())
    old = active_broker()
    if old not in OPTIONS:
        old = "Dhan"
    if target == old:
        st.session_state.pop(PENDING_KEY, None)
        return
    _request_cancel(old)
    if _busy_jobs(old):
        # Hold the old mode but block its NEW requests. Only after its queued
        # work finishes will the new broker be activated.
        st.session_state[PENDING_KEY] = target
        return
    st.session_state[ACTIVE_KEY] = target
    st.session_state.pop(PENDING_KEY, None)
    _apply_source_selection(target)


@st.fragment(run_every="2s")
def _pending_status():
    target = st.session_state.get(PENDING_KEY)
    if target not in OPTIONS:
        return
    previous = active_broker()
    _request_cancel(previous)
    pending = _busy_jobs(previous)
    if pending:
        st.caption(
            f"Switching to {target} · finishing {len(pending)} previous "
            "read-only broker request(s). New broker login is held."
        )
        return
    st.session_state[ACTIVE_KEY] = target
    st.session_state.pop(PENDING_KEY, None)
    _apply_source_selection(target)
    st.rerun(scope="app")


def render_single_broker_selector() -> None:
    st.session_state.setdefault(ACTIVE_KEY, "Dhan")
    st.session_state.setdefault(SELECTION_KEY, active_broker())
    # A pending switch updates ACTIVE_KEY, which is *not* widget-owned.
    # It can safely complete in a fragment rerun after previous jobs finish.
    _pending_status()
    st.radio(
        "Active broker connection · only one at a time",
        OPTIONS,
        key=SELECTION_KEY, horizontal=True, on_change=_switch_callback,
        help="Saved Dhan and FYERS credentials are preserved when you switch. "
             "The inactive broker sends no requests or token renewals. "
             "Existing broker-issued sessions are not remotely revoked.",
    )
    current = active_broker()
    if st.session_state.get(PENDING_KEY):
        st.info("Broker change in progress. No new broker login will start.")
    elif current == "FYERS":
        st.caption(
            "FYERS active for market data · Dhan login and balance polling "
            "paused; Dhan credentials remain saved locally."
        )
    elif current == "Dhan":
        st.caption(
            "Dhan active · your existing saved login is reused while valid. "
            "FYERS market requests are paused."
        )
    else:
        st.caption("Public NSE/Yahoo data only · both broker connections paused.")
