"""Offline checks for accepted-only mobile refresh persistence."""
import unittest
from unittest.mock import patch
from threading import Event
from datetime import datetime
import pandas as pd

from dhan_visual import mobile_refresh as refresh
from dhan_visual.mobile_refresh_ui import payload_for
from dhan_visual.workspace_store import validate
from dhan_visual.fyers_readonly import FyersDataError
from dhan_visual.selective_scan import new_snapshot
from test_selective_scan import access, quotes, call, straddle, levels
from types import SimpleNamespace


class MobileRefreshTests(unittest.TestCase):
    def setUp(self):
        self.filters = dict(expiry="DEC-26", maximum=7, minimum=2, r1_filter="Include all stocks")

    def test_incomplete_cannot_publish(self):
        snap = new_snapshot("key","DEC-26",7,2)
        with self.assertRaises(ValueError):
            payload_for(snap,pd.DataFrame())

    def test_complete_marker_and_extra_details_roundtrip(self):
        snap = new_snapshot("key","DEC-26",7,2)
        snap.update(complete=True, completed_at="2026-11-27T15:00:00+05:30")
        payload = payload_for(snap,pd.DataFrame([{"Stock":"TCS","CMP (₹)":1000}]))
        self.assertTrue(validate({"version":1,"snapshot":payload})["snapshot"]["complete"])

    def test_missing_cash_never_completes(self):
        available = [SimpleNamespace(entry=SimpleNamespace(symbol="TCS"))]
        with patch("dhan_visual.fyers_readonly.fetch_cash_quotes",return_value={}):
            with self.assertRaises(FyersDataError):
                refresh.collect(access(),available,self.filters,"key",Event(),lambda *a:None)

    def test_cancel_before_any_request(self):
        stop=Event()
        stop.set()
        available = [SimpleNamespace(entry=SimpleNamespace(symbol="TCS"))]
        with patch("dhan_visual.fyers_readonly.fetch_cash_quotes") as cash:
            with self.assertRaises(FyersDataError):
                refresh.collect(access(),available,self.filters,"key",stop,lambda *a:None)
            cash.assert_not_called()

    def test_worker_completes_without_touching_saved_state(self):
        from dhan_visual.dhan_market import DhanUnderlying
        from dhan_visual.screener import Underlying
        available=[DhanUnderlying(Underlying("TCS","TCS",{"DEC-26":100}),1)]
        seen=[]
        with patch("dhan_visual.fyers_readonly.fetch_cash_quotes",return_value={"TCS":quotes()["TCS"]}), \
             patch("dhan_visual.selective_scan.resolve_expiry_timestamp",return_value=("123","2026-12-29")), \
             patch("dhan_visual.selective_scan.fetch_chain",return_value=(call(),straddle())), \
             patch("dhan_visual.selective_scan.fetch_daily_levels",return_value=levels()):
            snap=refresh.collect(access(),available,self.filters,"key",Event(),lambda *args:seen.append(args))
        self.assertTrue(snap["complete"])
        self.assertEqual(seen[-1][0],100)
        self.assertEqual(snap["attempted"],{"TCS"})
        self.assertEqual(set(snap["levels"]),{"TCS"})

    def test_auto_start_without_any_saved_snapshot(self):
        from dhan_visual.mobile_refresh_ui import should_auto_start
        self.assertTrue(should_auto_start(None, "token", None))
        self.assertFalse(should_auto_start("token", "token", None))
        self.assertFalse(should_auto_start(None, "token", {"future": object()}))
        self.assertTrue(should_auto_start("token", "token", None, apply=True))

    def test_locked_summary_uses_saved_count_and_premium(self):
        from dhan_visual.mobile_refresh_ui import render_saved_summary
        saved = dict(complete=True, filters=self.filters,
                     rows=[{"Stock":"TCS","5%+ OTM CE (%)":3.5}])
        with patch("dhan_visual.mobile_refresh_ui.st.markdown") as markup:
            render_saved_summary(saved, {}, status="Cached data")
        html = markup.call_args.args[0]
        self.assertIn('1 <span>opportunities', html)
        self.assertIn("3.50%", html)
        self.assertIn("Cached data", html)

    def test_publication_replaces_not_appends_and_preserves_on_write_failure(self):
        from dhan_visual import mobile_refresh_ui as ui
        from dhan_visual.selective_scan import STATE
        candidate = new_snapshot("key","DEC-26",7,2)
        candidate.update(complete=True, completed_at="2026-11-27T15:00:00+05:30")
        frame = pd.DataFrame([{"Stock":"TCS","CMP (₹)":1000}])
        old = {"rows":[{"Stock":"INFY"}]}
        stored = {"snapshot":old, "remember":False}
        state = {ui.JOB:{"future":object()}, STATE:"previous"}
        def save(fn):
            fn(stored)
            return True
        with patch("dhan_visual.selective_scan._universe_rows", return_value=[]), \
             patch("dhan_visual.selective_scan.qualified_rows", return_value=frame), \
             patch.object(ui.st,"session_state",state), \
             patch.object(ui,"save_change",return_value=False):
            self.assertFalse(ui.publish_candidate(candidate, []))
            self.assertIs(stored["snapshot"],old)
            self.assertEqual(state[STATE],"previous")
            self.assertIn(ui.JOB,state)
        with patch("dhan_visual.selective_scan._universe_rows", return_value=[]), \
             patch("dhan_visual.selective_scan.qualified_rows", return_value=frame), \
             patch.object(ui.st,"session_state",state), \
             patch.object(ui,"save_change",side_effect=save):
            self.assertTrue(ui.publish_candidate(candidate, []))
        self.assertEqual([r["Stock"] for r in stored["snapshot"]["rows"]],["TCS"])
        self.assertIs(state[STATE],candidate)
        self.assertNotIn(ui.JOB,state)

    def test_startup_filters_use_first_expiry_and_requested_values(self):
        from dhan_visual.mobile_refresh_ui import startup_filters
        self.assertEqual(startup_filters(["DEC-26", "JAN-27", "FEB-27"]),
                         dict(expiry="DEC-26", maximum=7, minimum=5,
                              r1_filter="Include all stocks"))
        with self.assertRaises(ValueError):
            startup_filters([])

    def test_cash_retry_only_missing_symbols(self):
        from unittest.mock import Mock
        cancel = Mock()
        cancel.wait.return_value = False
        fetch = Mock(side_effect=[{"TCS": 1}, {"INFY": 2}])
        result = refresh.fetch_cash_batch(access(), ["TCS", "INFY"], cancel,
                                          lambda: None, fetch, lambda _: None)
        self.assertEqual(result, {"TCS": 1, "INFY": 2})
        self.assertEqual(fetch.call_args_list[1].args[1], ["INFY"])
        cancel.wait.assert_called_once_with(5)

    def test_cash_retry_transient_connection_once(self):
        from unittest.mock import Mock
        cancel = Mock()
        cancel.wait.return_value = False
        fetch = Mock(side_effect=[FyersDataError("FYERS market-data service is unreachable."), {"TCS": 1}])
        self.assertEqual(refresh.fetch_cash_batch(access(), ["TCS"], cancel,
                         lambda: None, fetch, lambda _: None), {"TCS": 1})
        self.assertEqual(fetch.call_count, 2)

    def test_cash_rate_limit_does_not_retry(self):
        from unittest.mock import Mock
        fetch = Mock(side_effect=FyersDataError("FYERS data rate limit (429). Stop and retry later."))
        cancel = Mock()
        with self.assertRaises(FyersDataError):
            refresh.fetch_cash_batch(access(), ["TCS"], cancel,
                                     lambda: None, fetch, lambda _: None)
        fetch.assert_called_once()
        cancel.wait.assert_not_called()

    def test_nifty_fpi_index_excluded_from_catalogue_and_cached_universe(self):
        from dhan_visual.screener import INDEX_SYMBOLS, Underlying
        from dhan_visual.selective_scan import _universe_rows
        from dhan_visual.dhan_market import DhanUnderlying
        self.assertIn("NIFTYFPI", INDEX_SYMBOLS)
        entries = [DhanUnderlying(Underlying(symbol, symbol, {"DEC-26": 100}), i)
                   for i, symbol in enumerate(("NIFTYFPI", "NIFTY", "TCS"), 1)]
        self.assertEqual([x.entry.symbol for x in _universe_rows(entries, "DEC-26")],
                         ["TCS"])

    def test_live_summary_has_single_progress_bar_and_preserves_cached_metrics(self):
        from unittest.mock import patch
        from dhan_visual.mobile_refresh_ui import render_saved_summary
        saved = {"filters": {"maximum": 7}, "complete": True,
                 "rows": [{"Stock": "TCS", "5%+ OTM CE (%)": 5.25}]}
        with patch("dhan_visual.mobile_refresh_ui.st.markdown") as markup:
            render_saved_summary(saved, {}, status="Option chains 78/135",
                                 progress=60, elapsed=142)
        html = markup.call_args.args[0]
        self.assertEqual(html.count('class="ccs-ios-progress"'), 1)
        self.assertIn('width:60%', html)
        self.assertIn("60% · Option chains 78/135", html)
        self.assertIn("Elapsed: 2m 22s", html)
        self.assertIn("5.25%", html)
        self.assertIn('1 <span>opportunities', html)
