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
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool):
            app = app_with_catalogue().run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        self.assertGreaterEqual(len(app.tabs), 2)
        self.assertEqual(app.radio(key="fno_price_provider").value, "Yahoo Finance")
        self.assertEqual(app.selectbox(key="fno_value_preset").value, "Below ₹7 lakh")
        self.assertGreaterEqual(len(app.dataframe), 1)
        for col in ("EMA 200", "EMA 50", "EMA 20", "EMA 10", "Monthly R1"):
            self.assertIn(col, app.dataframe[0].value.columns)

    def test_local_credentials_dispatch_entire_universe_without_refresh_click(self):
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        app.session_state["fno_price_provider"] = "Dhan"
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
        app.session_state["fno_price_provider"] = "Dhan"
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
        self.assertEqual(frame["Contract value (₹L)"].tolist(), [4.5, 6.75])
        app.toggle(key="fno_compact_contract_value").set_value(False).run()
        self.assertEqual(
            app.dataframe[0].value["Contract value (₹)"].tolist(),
            [450000., 675000.]
        )
        app.selectbox(key="fno_value_preset").set_value("All stocks").run()
        self.assertEqual(len(app.dataframe[0].value), 2)

    def test_switching_to_yahoo_starts_full_universe_in_background(self):
        app = app_with_catalogue()
        app.session_state["fno_price_provider"] = "Dhan"
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

    def test_atm_option_chain_not_called_until_load_button(self):
        app = app_with_catalogue()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.option_panel.load_atm_options") as atm:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            atm.assert_not_called()
            self.assertGreaterEqual(len(app.expander), 1)
            self.assertIsNotNone(app.button(key="fno_atm_load"))
            # Clicking without authentication should still never send requests.
            app.button(key="fno_atm_load").click().run()
            atm.assert_not_called()

    def test_atm_load_one_selected_stock_only(self):
        from dhan_visual.atm_options import AtmOptionQuote, OptionLeg
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "TEST_TOKEN_DO_NOT_USE_REAL"
        expected = AtmOptionQuote(
            "SBIN", "2026-11-26", Decimal("600"), Decimal("603"), 750,
            OptionLeg(Decimal("12"), Decimal("11"), Decimal("13")),
            OptionLeg(Decimal("14"), Decimal("13"), Decimal("15")), "mock",
        )
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.option_panel.read_client_id", return_value="1234567890"), \
             patch("dhan_visual.option_panel.load_atm_options", return_value=expected) as load:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            app.selectbox(key="fno_atm_symbol").set_value("SBIN").run()
            load.assert_not_called()
            app.button(key="fno_atm_load").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            load.assert_called_once()
            self.assertEqual(load.call_args.args[:2], ("SBIN", "NOV-26"))
            self.assertEqual(load.call_args.args[3], 1333)
            self.assertEqual(app.session_state["fno_atm_result"]["quote"].ce.ltp, Decimal("12"))

    def test_full_straddle_panel_never_calls_dhan_on_page_load(self):
        app = app_with_catalogue()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.straddle_panel.scan_all_straddles") as scan:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            scan.assert_not_called()
            self.assertIsNotNone(app.button(key="fno_straddle_start"))
            self.assertIsNotNone(app.button(key="fno_straddle_stop"))

    def test_single_start_scans_all_unfiltered_stock_underlyings(self):
        app = app_with_catalogue()
        # These credentials are synthetic and never sent to the broker.
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "OFFLINE_FAKE_TEST_TOKEN_LONG"
        app.session_state["fno_price_provider"] = "Dhan"
        pool, other = FakePool(), FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=other), \
             patch("dhan_visual.straddle_panel._straddle_pool", return_value=pool), \
             patch("dhan_visual.straddle_panel.read_client_id", return_value="1234567890"), \
             patch("dhan_visual.straddle_panel.is_configured", return_value=False):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            app.button(key="fno_straddle_start").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls), 1)
            args, kwargs = pool.calls[0]
            self.assertEqual(args[0].__name__, "scan_all_straddles")
            self.assertEqual(len(args[1]), 2)
            self.assertEqual(args[2], "NOV-26")
            app.run()
            self.assertEqual(len(pool.calls), 1)

    def test_long_quote_date_and_source_fields_not_in_primary_cash_table(self):
        app = app_with_catalogue()
        app.session_state["fno_price_provider"] = "Dhan"
        app.run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        cols = app.dataframe[0].value.columns.tolist()
        self.assertNotIn("Quote time", cols)
        self.assertNotIn("Price source", cols)

    def test_technical_columns_use_displayed_quote_and_are_not_required_to_render(self):
        from dhan_visual.technical_indicators import TechnicalLevels
        from dhan_visual.technical_ui import _tech_key
        app = app_with_catalogue()
        cid, token = "1234567890", "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        app.session_state["dhan_local_cid"] = cid
        app.session_state["dhan_local_access_token"] = token
        app.session_state["fno_price_provider"] = "Dhan"
        symbols = ",".join(sorted(x.entry.symbol for x in example()))
        universe_key = hashlib.sha256(symbols.encode()).hexdigest()
        auth_key = hashlib.sha256((cid + ":" + token + ":" + universe_key).encode()).hexdigest()
        app.session_state["fno_dhan_attempt_key"] = auth_key
        app.session_state["fno_dhan_snapshot"] = {
            "key":auth_key,
            "quotes":{
                "TCS":Quote(Decimal("101"),"test","Dhan LTP"),
                "SBIN":Quote(Decimal("90"),"test","Dhan LTP"),
            },
            "ids":{"TCS":11536,"SBIN":1333},
            "requested_at":"mock","errors":[],
        }
        app.session_state["fno_technicals"] = {
            "key": _tech_key(["TCS","SBIN"]),
            "values": {
                "TCS": TechnicalLevels(
                    Decimal("99"),Decimal("100"),Decimal("102"),Decimal("103"),
                    Decimal("110"),Decimal("100"),"2026-11-23","2026-11",
                )
            },
            "processed":1,"total":2,"failed":0,"status":"loading",
        }
        app.run()
        self.assertFalse(app.exception,[str(x.message) for x in app.exception])
        frame=app.dataframe[0].value
        tcs=frame.loc[frame["Symbol"]=="TCS"].iloc[0]
        self.assertEqual(tcs["EMA 200"],"✓ ₹99.00")
        self.assertEqual(tcs["EMA 50"],"✓ ₹100.00")
        self.assertEqual(tcs["EMA 20"],"✕ ₹102.00")
        self.assertEqual(tcs["Monthly R1"],"✕ ₹110.00")
        self.assertEqual(frame.loc[frame["Symbol"]=="SBIN"].iloc[0]["EMA 200"],"—")

    def test_technical_history_starts_only_after_yahoo_quote_loader_finishes(self):
        from dhan_visual.technical_ui import _tech_key
        from dhan_visual.screener_ui import _universe_key
        app=app_with_catalogue()
        symbols=[x.entry.symbol for x in example()]
        key=_universe_key(example())
        app.session_state["fno_yahoo_attempt_key"]=key
        app.session_state["fno_yahoo_snapshot"]={
            "key":key,"quotes":{},"errors":[],"processed":2,"total":2,
        }
        pool=FakePool()
        with patch("dhan_visual.technical_ui._tech_pool",return_value=pool):
            app.run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls),1)
            self.assertEqual(pool.calls[0][0][0].__name__,"load_all_technicals")
            self.assertEqual(set(pool.calls[0][0][1]),set(symbols))
            app.run()
            self.assertEqual(len(pool.calls),1)

    def test_no_manual_list_upload_or_50_stock_hard_cap(self):
        source = (APP.parent / "screener_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("file_uploader", source)
        self.assertNotIn("First 50 stocks", source)
        self.assertIn("load_fast_catalogue", source)
        self.assertIn("load_yahoo_all", source)


if __name__ == "__main__":
    unittest.main()
