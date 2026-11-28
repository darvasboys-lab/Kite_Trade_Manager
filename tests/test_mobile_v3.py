"""V3 cached reader uses real V2 data and pure payoff, never requests network."""
import tempfile
import unittest
from unittest.mock import patch
from dhan_visual import workspace_store as store, mobile_v3_core as core


class MobileV3Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict("os.environ", {"CCS_DATA_DIR":tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.data = store.read()
        self.data["snapshot"] = dict(saved_at="2026-11-27T15:25:00+05:30", complete=True,
            filters=dict(maximum=7,minimum=0,expiry="DEC-26",r1_filter="Include all stocks"),
            rows=[{"Stock":"TCS","CMP (₹)":1000,"Lot size":100,"CE strike":1050,
                   "CE premium (₹)":30,"ATM strike":1000,"ATM CE premium (₹)":40,
                   "ATM PE premium (₹)":35,"5%+ OTM CE (%)":3}])
        store.write(self.data)

    def test_cached_home_is_readonly(self):
        before = store.location().read_bytes()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("no network")):
            result = core.home()
        self.assertFalse(result["live_refresh"])
        self.assertEqual(result["snapshot"]["rows"][0]["Stock"],"TCS")
        self.assertEqual(before, store.location().read_bytes())

    def test_exact_covered_call(self):
        d = core.saved_payoff("TCS","Covered call")
        self.assertEqual(d["summary"]["max_profit"],8000)
        self.assertEqual(d["summary"]["breakevens"],[970.0])

    def test_exact_straddle_unlimited_loss(self):
        d = core.saved_payoff("TCS","Short straddle")
        self.assertEqual(d["summary"]["max_profit"],7500)
        self.assertEqual(d["summary"]["max_loss"],"Unlimited")

    def test_missing_stock_fails(self):
        with self.assertRaises(ValueError):
            core.saved_payoff("ABSENT","Covered call")

    def test_no_snapshot(self):
        store.write(dict(self.data,snapshot=None))
        self.assertIsNone(core.home()["snapshot"])


    def test_payoff_refuses_unaccepted_snapshot(self):
        with self.assertRaisesRegex(ValueError, "Accept the newer snapshot"):
            core.saved_payoff("TCS","Covered call","2026-11-26T15:25:00+05:30")

    def test_payoff_uses_accepted_snapshot(self):
        self.assertEqual(core.saved_payoff("TCS","Covered call",
            self.data["snapshot"]["saved_at"])["summary"]["max_profit"],8000)
