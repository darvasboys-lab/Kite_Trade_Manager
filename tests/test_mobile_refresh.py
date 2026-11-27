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
