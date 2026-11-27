"""Offline browser fixture: real app, synthetic quotes, no live broker requests."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import Future
from threading import Event
from dataclasses import replace
from unittest.mock import patch
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import streamlit as st
from dhan_visual import selective_scan as scan
from dhan_visual import stock_strategy_ui as strategy
from dhan_visual import mobile_refresh_ui as refresh
from dhan_visual.fyers_ui import _scope
from dhan_visual.screener import Underlying
from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.ce_eod_remote import IST
from test_selective_scan import access, quotes, call, straddle, levels

if not st.session_state.get("layout_fixture_initialized"):
    mode = st.query_params.get("layout_case", "accepted")
    now = datetime.now(IST)
    months = [datetime(now.year + (now.month - 1 + i) // 12,
                       (now.month - 1 + i) % 12 + 1, 1).strftime("%b-%y").upper()
              for i in range(3)]
    expiry = months[0]
    items = [DhanUnderlying(Underlying(s, s, {m: 100 for m in months}), i)
             for i, s in enumerate(("TCS", "SBIN"), 1)]
    token = access()
    snapshot = scan.new_snapshot(_scope(token, [], expiry), expiry, 7, 5)
    snapshot.update(cash=quotes(), calls={s:replace(call(s,"6"),expiry=expiry) for s in ("TCS","SBIN")},
                    straddles={"TCS":replace(straddle(),expiry=expiry)},
                    levels={"TCS":levels(),"SBIN":levels()},
                    attempted={"TCS","SBIN"}, history_attempted={"TCS","SBIN"},
                    levels_day=now.date().isoformat(), scan_day=now.date().isoformat(),
                    cash_at="offline", options_at="offline", complete=True,
                    completed_at=now.isoformat())
    st.session_state.update(layout_fixture_initialized=True, ccs_active_broker="FYERS",
                            fno_universe_data=(items,"Offline browser fixture"),
                            ccs_workspace_initialized=True, ccs_dark_mode=False)
    if mode != "locked":
        st.session_state["_fyers_access"] = token
        st.session_state[refresh.AUTO] = _scope(token, [], "")
    if mode == "accepted":
        st.session_state[scan.STATE] = snapshot
    if mode in ("loading","ready"):
        future = Future()
        if mode == "ready":
            future.set_result(snapshot)
        st.session_state[refresh.JOB] = {"future":future,"cancel":Event(),
            "key":snapshot["key"],"filters":{k:snapshot[k] for k in ("expiry","maximum","minimum","r1_filter")},
            "progress":(60,"Option chains 78/135")}
    st.session_state["layout_fixture_snapshot"] = snapshot
    st.session_state["layout_fixture_months"] = months

months = st.session_state["layout_fixture_months"]
cash = quotes()["TCS"]
chain = {(Decimal(s),kind):{"ltp":Decimal("60"),"oi":100,"bid":59,"ask":61}
         for s in ("950","1000","1050","1100") for kind in ("CE","PE")}
# Reject any accidental live HTTP. No credentials or real market data are used.
with patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden in layout fixture")), \
     patch.object(strategy,"fetch_strategy_expiries",return_value={m:("123",m) for m in months}), \
     patch.object(strategy,"fetch_strategy_quotes",return_value={"SPOT":cash,**{m:cash for m in months}}), \
     patch.object(strategy,"fetch_strategy_chain",return_value=chain):
    runpy.run_path(str(ROOT / "dhan_visual" / "app.py"), run_name="__main__")
