"""Mobile-only refresh and atomic publication of complete scans."""
from datetime import datetime
import json
from html import escape
import time
import pandas as pd
import streamlit as st
from .ce_eod_remote import IST
from .mobile_refresh import JOB, start
from .workspace_ui import load_workspace, save_change
from .fyers_ui import connected_fyers, _scope

AUTO = "ccs_mobile_auto_started"
ERROR = "ccs_mobile_refresh_error"


def payload_for(snapshot, frame):
    if not snapshot.get("complete") or snapshot.get("stopped"):
        raise ValueError("Cannot publish an incomplete snapshot.")
    rows = json.loads(frame.to_json(orient="records"))
    for row in rows:
        symbol = row["Stock"]
        call = snapshot["calls"].get(symbol)
        atm = snapshot["straddles"].get(symbol)
        levels = snapshot["levels"].get(symbol)
        if call is not None:
            row.update({"CE strike":float(call.strike),"CE premium (₹)":float(call.call_ltp)})
        if atm is not None:
            row.update({"ATM strike":float(atm.strike),"ATM CE premium (₹)":float(atm.call_ltp),
                        "ATM PE premium (₹)":float(atm.put_ltp)})
        if levels is not None:
            for label, attr in (("EMA 200","ema200"),("EMA 50","ema50"),
                                ("EMA 20","ema20"),("EMA 10","ema10"),("Monthly R1","monthly_r1")):
                val = getattr(levels,attr,None)
                if val is not None:
                    row[label] = float(val)
    return dict(saved_at=snapshot["completed_at"], complete=True,
                filters={k:snapshot[k] for k in ("maximum","minimum","expiry","r1_filter")},
                rows=rows)



def startup_filters(expiries):
    """Defaults for each new mobile Home session, independent of the cache."""
    if not expiries:
        raise ValueError("No verified expiry is available.")
    return dict(expiry=expiries[0], maximum=7, minimum=0,
                r1_filter="Include all stocks")


def should_auto_start(previous_scope, token_scope, job, apply=False):
    return not job and (apply or previous_scope != token_scope)


def render_saved_summary(shown, filters, *, status=None, progress=None, elapsed=None):
    """One summary for locked, loading and connected Home."""
    rows = (shown or {}).get("rows", [])
    vals = [r.get("5%+ OTM CE (%)") for r in rows]
    vals = [v for v in vals if isinstance(v, (int, float))]
    best = f"{max(vals):.2f}%" if vals else "—"
    maximum = (shown or {}).get("filters", filters).get("maximum", 7)
    count = str(len(rows)) if shown is not None else "—"
    ratio = "100%" if shown and shown.get("complete") else "0%"
    if status is None:
        status = "Last accepted scan" if shown and shown.get("complete") else "Saved / awaiting verified scan"
    if progress is not None:
        progress = max(0, min(100, int(progress)))
        ratio = f"{progress}%"
        status = f"{progress}% · {status}"
    elapsed_html = (f'<small class="ccs-ios-refresh-elapsed">Elapsed: {elapsed // 60}m {elapsed % 60:02d}s</small>'
                    if elapsed is not None else "")
    st.markdown(
        '<div class="ccs-ios-summary"><small>OTM COVERED CALL SCANNER</small>'
        f'<div class="ccs-ios-count">{count} <span>opportunities</span></div>'
        f'<div class="ccs-ios-progress"><i style="width:{ratio}"></i></div>'
        f'<small class="ccs-ios-refresh-status">{escape(status)}</small>{elapsed_html}'
        '<div class="ccs-ios-summarygrid">'
        f'<div><small>Highest CE premium</small><strong>{best}</strong></div>'
        f'<div><small>Contract limit</small><strong>₹{int(maximum)} lakh</strong></div></div></div>',
        unsafe_allow_html=True)


def publish_candidate(candidate, universe, *, initial=False):
    from .selective_scan import STATE, _universe_rows, qualified_rows
    frame = qualified_rows(_universe_rows(universe, candidate["expiry"]), candidate)
    payload = payload_for(candidate, frame)
    def publish(data):
        if initial and data.get("snapshot") is not None:
            raise ValueError("Initial snapshot already exists.")
        data["snapshot"] = payload
        if data["remember"]:
            data["preferences"]["filters"] = payload["filters"]
    if not save_change(publish):
        return False
    st.session_state[STATE] = candidate
    st.session_state.pop(JOB, None)
    st.session_state.pop(ERROR, None)
    return True


def begin(access, universe, filters):
    from .selective_scan import _universe_rows
    old = st.session_state.get(JOB)
    if old:
        st.session_state[ERROR] = "Finish, accept or discard the current refresh first."
        return
    job = start(access, _universe_rows(universe, filters["expiry"]), filters,
                _scope(access, [], filters["expiry"]))
    if job is None:
        st.session_state[ERROR] = "Another refresh is running. Retry when it finishes."
        return
    job["started_at"] = time.monotonic()
    st.session_state[JOB] = job
    st.session_state.pop(ERROR,None)


def refresh_panel(universe, shown, filters):
    job = st.session_state.get(JOB)
    access = connected_fyers()
    if not job:
        render_saved_summary(shown, filters)
        if st.session_state.get(ERROR):
            st.warning(st.session_state[ERROR])
        return
    future = job["future"]
    if not access or job["key"] != _scope(access, [], job["filters"]["expiry"]):
        job["cancel"].set()
        render_saved_summary(shown, filters, status="Stopping previous refresh")
        if future.done():
            st.session_state.pop(JOB,None)
            st.session_state.pop(AUTO,None)
            st.rerun(scope="app")
        return
    if not future.done():
        percent, stage = job["progress"]
        elapsed = max(0, int(time.monotonic() - job.get("started_at", time.monotonic())))
        render_saved_summary(shown, filters, status=stage, progress=min(percent, 99), elapsed=elapsed)
        if st.button("Cancel refresh", key="ccs_cancel_refresh"):
            job["cancel"].set()
        return
    try:
        candidate = future.result()
        if job["cancel"].is_set() or not candidate.get("complete") or candidate.get("stopped"):
            raise ValueError("Refresh cancelled")
        if candidate["scan_day"] != datetime.now(IST).date().isoformat():
            raise ValueError("Refresh expired")
    except Exception as exc:
        from .fyers_readonly import FyersDataError
        msg = str(exc) if isinstance(exc,FyersDataError) else "Refresh incomplete or cancelled."
        st.session_state[ERROR] = msg + " Last saved results kept."
        st.session_state.pop(JOB,None)
        st.rerun(scope="app")
        return
    # Every completed scan, including the first, awaits explicit acceptance.
    render_saved_summary(shown, filters, status="Fresh data ready · tap Update now", progress=100)
    with st.container(key="ccs_refresh_ready", border=True):
        st.markdown("**✓ Fresh data available**")
        st.caption("100% · All scan stages finished. Tap Update now to display and save the complete results.")
        if st.button("Update now", key="ccs_apply_refresh", type="primary", use_container_width=True):
            if publish_candidate(candidate, universe):
                st.rerun(scope="app")
        if st.button("Keep old data", key="ccs_discard_refresh"):
            st.session_state.pop(JOB,None)
            st.rerun(scope="app")


@st.fragment(run_every="2s")
def _poll_refresh_panel(universe, shown, filters):
    refresh_panel(universe, shown, filters)


def render_mobile_refresh(access, universe, filters, apply):
    from .selective_scan import (_snapshot, _universe_rows, qualified_rows,
                                 render_ios_saved_shortlist)
    data = load_workspace() or {}
    saved = data.get("snapshot")
    active = _snapshot(access)
    if active and not active.get("complete"):
        active = None
    # Only once per connected session/authorization, never on a timed rerun.
    token_scope = _scope(access, [], "")
    if should_auto_start(st.session_state.get(AUTO), token_scope, st.session_state.get(JOB), apply):
        st.session_state[AUTO] = token_scope
        begin(access, universe, filters)
    elif apply and st.session_state.get(JOB):
        st.session_state[ERROR] = "Finish, accept or discard the current refresh first."
    if active:
        available = _universe_rows(universe,active["expiry"])
        frame = qualified_rows(available,active)
        shown = payload_for(active, frame)
    else:
        shown = saved
    with st.container(key="ccs_mobile_refresh_panel"):
        if st.session_state.get(JOB):
            _poll_refresh_panel(universe, shown, filters)
        else:
            refresh_panel(universe, shown, filters)
    with st.container(key="ccs_mobile_results"):
        if shown:
            timestamp = datetime.fromisoformat(shown["saved_at"]).astimezone(IST)
            label = "Accepted snapshot" if active else "Cached data"
            if not shown.get("complete"):
                label += " · previous scan completeness unverified"
            st.caption(f"{label} · {timestamp:%d %b %H:%M IST} · {shown['filters']['expiry']}")
            cutoff = shown["filters"]["minimum"]
            premium_label = f"CE premium > {cutoff}%" if cutoff else "Any quoted CE premium"
            st.caption(f"Strike ≥ 5% above CMP · {premium_label} · contract < ₹{shown['filters']['maximum']}L")
            if active:
                st.caption(f"{len(active['cash'])} stock prices loaded · {len(active['attempted'])} option chains checked · {len(frame)} matches")
        job = st.session_state.get(JOB)
        if not job and st.button("Retry refresh" if st.session_state.get(ERROR) else "Refresh data", key="ccs_mobile_refresh", use_container_width=True):
            st.session_state[AUTO] = token_scope
            begin(access,universe,filters)
            st.rerun()
        context = (({item.entry.symbol:item.entry for item in available},
                    active["expiry"],active["key"],active["scan_day"]) if active else None)
        render_ios_saved_shortlist(shown, strategy_context=context)
