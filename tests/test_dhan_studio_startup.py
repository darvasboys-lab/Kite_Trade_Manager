"""Browserless Streamlit tests: auto-fetch jobs and visible values, no live network."""
import hashlib
import unittest
from concurrent.futures import Future
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.screener import Quote, Underlying

APP = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"


def example():
    return [
        DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26": 225, "DEC-26": 225}), 11536),
        DhanUnderlying(Underlying("SBIN", "SBIN", {"NOV-26": 750, "DEC-26": 750}), 1333),
    ]


def app_with_catalogue():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(APP), default_timeout=35)
    app.session_state["fno_universe_data"] = (example(), "Verified unit fixture")
    return app


class FakePool:
    def __init__(self):
        self.calls = []
        self.future = Future()  # never triggers live Dhan/Yahoo requests

    def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.future


class PayoffStudioStartupTests(unittest.TestCase):
    def test_main_dashboard_executes_without_exception(self):
        app = app_with_catalogue().run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        self.assertGreaterEqual(len(app.tabs), 2)
        self.assertEqual(app.radio(key="fno_price_provider").value, "Dhan")
        self.assertEqual(app.selectbox(key="fno_value_preset").value, "Below ₹7 lakh")
        self.assertGreaterEqual(len(app.dataframe), 1)

    def test_local_credentials_dispatch_entire_universe_without_refresh_click(self):
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls), 1)
            args, kwargs = pool.calls[0]
            self.assertEqual(args[0].__name__, "load_dhan_all")
            self.assertEqual(len(args[1]), len(example()))
            self.assertEqual(app.session_state["fno_dhan_job"]["future"], pool.future)
            app.run()
        self.assertEqual(len(pool.calls), 1, "No duplicate network job on Streamlit rerun")

    def test_cached_prices_render_contract_notional_and_below_7l_sort(self):
        app = app_with_catalogue()
        cid, token = "1234567890", "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        app.session_state["dhan_local_cid"] = cid
        app.session_state["dhan_local_access_token"] = token
        symbols = ",".join(sorted(x.entry.symbol for x in example()))
        universe_key = hashlib.sha256(symbols.encode()).hexdigest()
        key = hashlib.sha256((cid + ":" + token + ":" + universe_key).encode()).hexdigest()
        app.session_state["fno_dhan_attempt_key"] = key
        app.session_state["fno_dhan_snapshot"] = {
            "key": key,
            "quotes": {
                "TCS": Quote(Decimal("3000"), "test", "Dhan LTP"),
                "SBIN": Quote(Decimal("600"), "test", "Dhan LTP"),
            },
            "ids": {"TCS": 11536, "SBIN": 1333},
            "errors": [], "requested_at": "mock",
        }
        app.run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        frame = app.dataframe[0].value
        self.assertEqual(frame["Symbol"].tolist(), ["SBIN", "TCS"])
        self.assertEqual(frame["Contract value (₹)"].tolist(), [450000., 675000.])
        app.selectbox(key="fno_value_preset").set_value("All stocks").run()
        self.assertEqual(len(app.dataframe[0].value), 2)

    def test_switching_to_yahoo_starts_full_universe_in_background(self):
        app = app_with_catalogue()
        app.run()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool):
            app.radio(key="fno_price_provider").set_value("Yahoo Finance").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls), 1)
            args, kwargs = pool.calls[0]
            self.assertEqual(args[0].__name__, "load_yahoo_all")
            self.assertEqual(len(args[1]), len(example()))
            self.assertEqual(args[-1], 25)

    def test_no_manual_list_upload_or_50_stock_hard_cap(self):
        source = (APP.parent / "screener_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("file_uploader", source)
        self.assertNotIn("First 50 stocks", source)
        self.assertIn("load_fast_catalogue", source)
        self.assertIn("load_yahoo_all", source)


if __name__ == "__main__":
    unittest.main()
