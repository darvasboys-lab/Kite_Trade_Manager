"""Browserless Streamlit tests: auto-fetch jobs and visible values, no live network."""
import hashlib
import unittest
from concurrent.futures import Future
from decimal import Decimal
from datetime import datetime
from dhan_visual.ce_eod_remote import EodDownloadResult, IST
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
    # No exchange or Dhan HTTP in offline CI tests.
    app.session_state["fno_ce5_remote_attempt_key"] = datetime.now(IST).date().isoformat()
    app.session_state["fno_ce5_remote_snapshot"] = EodDownloadResult(
        None, "", "Offline fixture: no remote report requested", ()
    )
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
        self.assertEqual(app.radio(key="fno_price_provider").value, "Auto (Dhan → Yahoo)")
        self.assertEqual(app.selectbox(key="fno_value_preset").value, "Below ₹7 lakh")
        self.assertGreaterEqual(len(app.dataframe), 1)
        self.assertNotIn("Company", app.dataframe[0].value.columns)
        self.assertIn("Symbol", app.dataframe[0].value.columns)
        self.assertIn("TradingView chart", app.dataframe[0].value.columns)
        from urllib.parse import parse_qs, urlsplit
        df = app.dataframe[0].value
        for _, item in df.iterrows():
            url = item["TradingView chart"]
            self.assertEqual(urlsplit(url).netloc, "www.tradingview.com")
            self.assertEqual(
                parse_qs(urlsplit(url).query)["symbol"],
                ["NSE:" + item["Symbol"]],
            )
        self.assertIsNotNone(app.button(key="ccs_autofit_all_button"))
        for col in ("EMA 200", "EMA 50", "EMA 20", "EMA 10", "Monthly R1",
                    "5%+ CE premium (%)"):
            self.assertIn(col, app.dataframe[0].value.columns)
        # Former full-width filter, search and quote source controls remain
        # reachable by exactly the same widget keys inside small popovers.
        self.assertEqual(app.radio(key="fno_value_sort").value, "Low → high")
        self.assertIsNotNone(app.text_input(key="fno_symbol_find"))
        self.assertIsNotNone(app.button(key="fno_manual_price_refresh"))
        self.assertIsNotNone(app.button(key="fno_refresh_universe"))

    def test_clickable_stock_column_is_link_not_duplicate_company(self):
        app = app_with_catalogue()
        with patch("dhan_visual.screener_ui._market_pool", return_value=FakePool()):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            frame = app.dataframe[0].value
            self.assertEqual(
                frame.columns.tolist()[:2], ["TradingView chart", "Symbol"]
            )
            self.assertNotIn("Company", frame.columns)
            self.assertTrue(frame["TradingView chart"].str.startswith(
                "https://www.tradingview.com/chart/?symbol=NSE%3A"
            ).all())
        # Raw stock symbols remain the same for search, sorting and CSV.
        source = (APP.parent / "screener_ui.py").read_text(encoding="utf-8")
        self.assertIn('config["Symbol"] = None', source)
        self.assertIn("st.column_config.LinkColumn(", source)
        self.assertIn("display_text=TRADINGVIEW_SYMBOL_DISPLAY", source)

    def test_r1_today_panel_does_not_request_yahoo_before_monthly_r1_ready(self):
        app = app_with_catalogue()
        quote_pool = FakePool()
        cross_pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=quote_pool), \
             patch("dhan_visual.r1_today_panel._r1_pool", return_value=cross_pool):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(len(cross_pool.calls), 0)
            self.assertIsNotNone(app.button(key="ccs_r1_today_refresh"))
            # The existing strategy-builder tab has its own independent table.
            self.assertGreaterEqual(len(app.dataframe), 1)
            self.assertIn("TradingView chart", app.dataframe[0].value.columns)

    def test_r1_today_auto_scan_all_fno_underlyings_not_current_filters(self):
        from dhan_visual.technical_indicators import TechnicalLevels
        from dhan_visual.technical_ui import _tech_key
        app = app_with_catalogue()
        app.session_state["fno_symbol_find"] = "TCS"
        app.session_state["fno_value_preset"] = "Below ₹5 lakh"
        app.session_state["fno_technicals"] = {
            "key": _tech_key(["TCS", "SBIN"]),
            "values": {
                sym: TechnicalLevels(
                    Decimal("90"),Decimal("94"),Decimal("95"),Decimal("96"),
                    Decimal("100"),Decimal("101"),"2026-11-23","2026-11"
                ) for sym in ("TCS","SBIN")
            },
            "stage":4,"stage_next_at":float("inf"),
            "processed":2,"total":2,"failed":0,"status":"complete",
        }
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool",return_value=FakePool()), \
             patch("dhan_visual.r1_today_panel._r1_pool",return_value=pool):
            app.run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls),1)
            args,kwargs = pool.calls[0]
            self.assertEqual(args[0].__name__,"scan_r1_today")
            self.assertEqual(set(args[1]),{"TCS","SBIN"})
            self.assertEqual(set(args[2]),{"TCS","SBIN"})
            self.assertEqual(args[2]["TCS"],Decimal("100"))
            app.run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(len(pool.calls),1, "Do not repeat Yahoo scan on widget reruns")

    def test_one_click_autofits_every_table_without_changing_stock_data(self):
        from dhan_visual.table_layout import dataframe_key
        app = app_with_catalogue()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            before = app.dataframe[0].value.copy()
            self.assertNotIn("Company", before.columns)
            app.button(key="ccs_autofit_all_button").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertTrue(app.session_state["ccs_autofit_all"])
            self.assertEqual(app.session_state["ccs_autofit_revision"], 1)
            self.assertEqual(
                dataframe_key("ccs_stock_screener", app.session_state),
                "ccs_stock_screener_autofit_1",
            )
            self.assertEqual(
                dataframe_key("ccs_straddle_rankings", app.session_state),
                "ccs_straddle_rankings_autofit_1",
            )
            self.assertEqual(
                dataframe_key("ccs_covered_call_stages", app.session_state),
                "ccs_covered_call_stages_autofit_1",
            )
            self.assertTrue(app.dataframe[0].value.equals(before))
            app.button(key="ccs_autofit_all_button").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.session_state["ccs_autofit_revision"], 2)

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

    def test_dhan_profile_button_requires_explicit_click(self):
        """Only a user click may call GET /v2/profile."""
        app = app_with_catalogue()
        with patch("dhan_visual.screener_ui._market_pool", return_value=FakePool()), \
             patch("dhan_visual.option_panel.check_dhan_data_plan") as profile:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertIsNotNone(app.button(key="fno_dhan_profile_check"))
            profile.assert_not_called()
            app.button(key="fno_dhan_profile_check").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            profile.assert_not_called()

    def test_single_dhan_chain_verifies_both_straddle_and_otm_ce(self):
        """One click populates main Live CE without a second broker request."""
        from dhan_visual.atm_options import AtmOptionQuote, OptionLeg
        app = app_with_catalogue()
        app.session_state["fno_expiry"] = "DEC-26"
        app.session_state["fno_ce5_mode"] = "Live"
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "SYNTHETIC_NO_LIVE_CONNECTION"
        expected = AtmOptionQuote(
            "TCS", "2026-12-29", Decimal("100"),
            Decimal("100"), 225,
            OptionLeg(Decimal("4"), Decimal("3.5"), Decimal("4.5")),
            OptionLeg(Decimal("3"), Decimal("2.5"), Decimal("3.5")),
            "25 Nov 2026 09:30 IST",
            Decimal("105"),
            OptionLeg(Decimal("2"), Decimal("1.5"), Decimal("2.5")),
        )
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.option_panel.read_client_id", return_value="1234567890"), \
             patch("dhan_visual.option_panel.load_atm_options", return_value=expected) as chain, \
             patch("dhan_visual.straddle_panel.scan_all_straddles") as bulk:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertTrue(app.dataframe[0].value["5%+ CE premium (%)"].isna().all())
            app.selectbox(key="fno_atm_symbol").set_value("TCS").run()
            app.button(key="fno_atm_load").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            chain.assert_called_once()
            self.assertEqual(chain.call_args.args[0:2], ("TCS", "DEC-26"))
            self.assertEqual(
                app.session_state["fno_atm_result"]["cid"], "1234567890"
            )
            frame = app.dataframe[0].value
            self.assertEqual(
                frame.loc[frame["Symbol"] == "TCS",
                          "5%+ CE premium (%)"].iloc[0],
                2.0,
            )
            self.assertIn(
                "Dhan LTP", frame.loc[frame["Symbol"] == "TCS",
                                     "CE source"].iloc[0]
            )
            bulk.assert_not_called()

    def test_successful_dhan_integration_shows_big_green_tick_then_skips_setup(self):
        """Profile + one stock proves onboarding once, persisting no secrets."""
        from datetime import timedelta
        from tempfile import TemporaryDirectory
        from dhan_visual.atm_options import AtmOptionQuote, OptionLeg
        from dhan_visual.dhan_readonly_check import DhanProfileStatus
        from dhan_visual.dhan_integration_state import (
            IST, record_verification, verified_account,
        )
        client = "1234567890"
        token = "TEST_TOKEN_NOT_FOR_PRODUCTION"
        expiry_text = (datetime.now(IST) + timedelta(hours=15)).strftime(
            "%d/%m/%Y %H:%M"
        )
        active_profile = DhanProfileStatus(
            "Active", "2026-12-31 20:00:00.0", expiry_text,
            True, "2026-11-25T09:40:00+00:00",
        )
        chain = AtmOptionQuote(
            "TCS", "2026-12-29", Decimal("100"), Decimal("100"), 225,
            OptionLeg(Decimal("4"), Decimal("3"), Decimal("5")),
            OptionLeg(Decimal("3"), Decimal("2"), Decimal("4")),
            "Offline Mock Only", Decimal("105"),
            OptionLeg(Decimal("2"), Decimal("1.5"), Decimal("2.5")),
        )
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "verified.json"
            app = app_with_catalogue()
            app.session_state["fno_expiry"] = "DEC-26"
            app.session_state["fno_ce5_mode"] = "Live"
            app.session_state["dhan_local_cid"] = client
            app.session_state["dhan_local_access_token"] = token
            # Explicit opt-out: do not save a test key in the OS credential vault.
            app.session_state["dhan_remember_manual_token"] = False
            with patch("dhan_visual.screener_ui.verified_account",
                       side_effect=lambda cid: verified_account(cid, state_path)), \
                 patch("dhan_visual.option_panel.verified_account",
                       side_effect=lambda cid: verified_account(cid, state_path)), \
                 patch("dhan_visual.option_panel.record_verification",
                       side_effect=lambda cid, sym, exp, validity: record_verification(
                           cid, sym, exp, validity, path=state_path
                       )) as record, \
                 patch("dhan_visual.screener_ui.load_manual_token", return_value=""), \
                 patch("dhan_visual.option_panel.load_manual_token", return_value=""), \
                 patch("dhan_visual.option_panel.check_dhan_data_plan",
                       return_value=active_profile) as profile, \
                 patch("dhan_visual.option_panel.load_atm_options",
                       return_value=chain) as load, \
                 patch("dhan_visual.option_panel.save_manual_token") as vault, \
                 patch("dhan_visual.screener_ui._market_pool",
                       return_value=FakePool()), \
                 patch("dhan_visual.straddle_panel.scan_all_straddles") as bulk:
                app.run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                profile.assert_not_called()
                load.assert_not_called()
                app.selectbox(key="fno_atm_symbol").set_value("TCS").run()
                app.button(key="fno_dhan_profile_check").click().run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                profile.assert_called_once()
                load.assert_not_called()
                app.button(key="fno_atm_load").click().run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                load.assert_called_once()
                record.assert_called_once()
                vault.assert_not_called()  # no vault without opt-in
                self.assertTrue(state_path.exists())
                self.assertIsNotNone(verified_account(client, state_path))
                self.assertNotIn(token, state_path.read_text(encoding="utf8"))
                self.assertTrue(any(
                    "Dhan integration completed" in str(node.value)
                    for node in app.markdown
                ))
                # No second profile check or one-stock onboarding on reruns.
                app.run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                profile.assert_called_once()
                load.assert_called_once()
                bulk.assert_not_called()
                self.assertIsNotNone(app.button(key="fno_dhan_optional_recheck"))

                # A fresh browser session sees the saved integration badge.
                second = app_with_catalogue()
                second.session_state["dhan_local_cid"] = client
                second.run()
                self.assertFalse(second.exception, [
                    str(x.message) for x in second.exception
                ])
                self.assertTrue(any(
                    "Dhan integration completed" in str(node.value)
                    for node in second.markdown
                ))
                profile.assert_called_once()
                load.assert_called_once()

    def test_option_chain_without_active_profile_does_not_complete_setup(self):
        """A valid-looking CE fixture alone cannot mint a success badge."""
        from dhan_visual.atm_options import AtmOptionQuote, OptionLeg
        from dhan_visual.dhan_readonly_check import DhanProfileStatus
        from tempfile import TemporaryDirectory
        from dhan_visual.dhan_integration_state import verified_account
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "FAKE_TEST_TOKEN_LONG"
        chain = AtmOptionQuote(
            "TCS", "2026-11-26", Decimal("100"), Decimal("100"), 225,
            OptionLeg(Decimal("3"), None, None),
            OptionLeg(Decimal("3"), None, None), "mock",
            Decimal("105"), OptionLeg(Decimal("2"), None, None),
        )
        with TemporaryDirectory() as directory:
            state = Path(directory) / "verified.json"
            with patch("dhan_visual.screener_ui.verified_account",
                       side_effect=lambda cid: verified_account(cid, state)), \
                 patch("dhan_visual.option_panel.verified_account",
                       side_effect=lambda cid: verified_account(cid, state)), \
                 patch("dhan_visual.option_panel.record_verification") as record, \
                 patch("dhan_visual.option_panel.load_atm_options", return_value=chain), \
                 patch("dhan_visual.option_panel.check_dhan_data_plan") as profile, \
                 patch("dhan_visual.screener_ui._market_pool",
                       return_value=FakePool()):
                app.run()
                app.button(key="fno_atm_load").click().run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                profile.assert_not_called()
                record.assert_not_called()
                self.assertFalse(any(
                    "Dhan integration completed" in str(node.value)
                    for node in app.markdown
                ))

    def test_verified_dhan_defaults_to_cash_ltp_funds_and_one_auto_ce_scan(self):
        """Zero extra broker calls per rerun, 5% CE uses paced option-chain job."""
        from datetime import timedelta
        from dhan_visual.dhan_integration_state import VerifiedIntegration, IST
        import time
        verified = VerifiedIntegration(
            client_id="1234567890",
            verified_at=datetime.now(IST).isoformat(),
            symbol="TCS", expiry="2026-12-29", data_plan="Active",
            token_expires_at=(datetime.now(IST) + timedelta(hours=12)).isoformat(),
        )
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "OFFLINE_TOKEN_LONG_NEVER_LIVE"
        app.session_state["fno_ce5_mode"] = "Live"
        dhan, funds, straddle, yahoo = (
            FakePool(), FakePool(), FakePool(), FakePool()
        )
        with patch("dhan_visual.screener_ui.verified_account",
                   return_value=verified), \
             patch("dhan_visual.option_panel.verified_account",
                   return_value=verified), \
             patch("dhan_visual.straddle_panel.verified_account",
                   return_value=verified), \
             patch("dhan_visual.screener_ui.load_manual_token", return_value=""), \
             patch("dhan_visual.option_panel.load_manual_token", return_value=""), \
             patch("dhan_visual.screener_ui._market_pool",
                   return_value=dhan), \
             patch("dhan_visual.screener_ui._funds_pool",
                   return_value=funds), \
             patch("dhan_visual.straddle_panel._straddle_pool",
                   return_value=straddle), \
             patch("dhan_visual.straddle_panel.nse_regular_session",
                   return_value=True):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="fno_price_provider").value,
                             "Auto (Dhan → Yahoo)")
            self.assertEqual(len(funds.calls), 1)
            self.assertEqual(funds.calls[0][0][0].__name__, "read_dhan_balance")
            self.assertEqual(len(straddle.calls), 1)
            self.assertEqual(straddle.calls[0][0][0].__name__, "scan_all_straddles")
            self.assertEqual(len(dhan.calls), 1)
            self.assertEqual(dhan.calls[0][0][0].__name__, "load_dhan_all")
            self.assertIsNotNone(app.button(key="fno_straddle_stop"))
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(len(straddle.calls), 1)
            self.assertEqual(len(dhan.calls), 1)
            self.assertEqual(len(funds.calls), 1)

    def test_auto_unverified_uses_yahoo_without_dhan_or_funds_or_option_scan(self):
        """Unverified accounts never make automatic broker GET/POST requests."""
        app = app_with_catalogue()
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "FAKE_UNVERIFIED_TOKEN_LONG"
        pool, funds, straddle = FakePool(), FakePool(), FakePool()
        with patch("dhan_visual.screener_ui.verified_account", return_value=None), \
             patch("dhan_visual.straddle_panel.verified_account",
                   return_value=None), \
             patch("dhan_visual.screener_ui._market_pool",
                   return_value=pool), \
             patch("dhan_visual.screener_ui._funds_pool",
                   return_value=funds), \
             patch("dhan_visual.straddle_panel._straddle_pool",
                   return_value=straddle):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(pool.calls[0][0][0].__name__, "load_yahoo_all")
            self.assertEqual(len(funds.calls), 0)
            self.assertEqual(len(straddle.calls), 0)

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
            "stage":0,"stage_next_at":float("inf"),
        }
        app.run()
        self.assertFalse(app.exception,[str(x.message) for x in app.exception])
        frame=app.dataframe[0].value
        tcs=frame.loc[frame["Symbol"]=="TCS"].iloc[0]
        self.assertEqual(tcs["EMA 200"],"☑ ₹99.00")
        self.assertEqual(tcs["EMA 50"],"—")
        self.assertEqual(tcs["EMA 20"],"—")
        self.assertEqual(tcs["Monthly R1"],"—")
        self.assertEqual(frame.loc[frame["Symbol"]=="SBIN"].iloc[0]["EMA 200"],"—")
        app.session_state["fno_technicals"]["stage"]=1
        app.run()
        self.assertEqual(
            app.dataframe[0].value.loc[
                app.dataframe[0].value["Symbol"]=="TCS", "EMA 50"
            ].iloc[0], "☑ ₹100.00"
        )
        self.assertEqual(
            app.dataframe[0].value.loc[
                app.dataframe[0].value["Symbol"]=="TCS", "EMA 20"
            ].iloc[0], "—"
        )
        app.session_state["fno_technicals"]["stage"]=4
        app.run()
        frame=app.dataframe[0].value
        tcs=frame.loc[frame["Symbol"]=="TCS"].iloc[0]
        self.assertEqual(tcs["EMA 20"],"₹102.00")
        self.assertEqual(tcs["EMA 10"],"₹103.00")
        self.assertEqual(tcs["Monthly R1"],"₹110.00")
        self.assertNotIn("✕", frame.to_string())

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

    def test_otm_ce_column_populates_from_existing_scan_without_new_broker_calls(self):
        from dhan_visual.straddle_scan import OtmCall5Quote
        app = app_with_catalogue()
        cid, token = "1234567890", "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        app.session_state["dhan_local_cid"] = cid
        app.session_state["dhan_local_access_token"] = token
        app.session_state["fno_price_provider"] = "Dhan"
        app.session_state["fno_ce5_mode"] = "Live"
        symbols = ",".join(sorted(x.entry.symbol for x in example()))
        universe_key = hashlib.sha256(symbols.encode()).hexdigest()
        key = hashlib.sha256((cid + ":" + token + ":" + universe_key).encode()).hexdigest()
        app.session_state["fno_dhan_attempt_key"] = key
        app.session_state["fno_dhan_snapshot"] = {
            "key": key, "quotes": {
                "TCS": Quote(Decimal("100"), "mock", "Dhan LTP"),
                "SBIN": Quote(Decimal("95"), "mock", "Dhan LTP"),
            }, "ids": {"TCS": 11536, "SBIN": 1333},
            "errors": [], "requested_at": "mock",
        }
        otm = OtmCall5Quote(
            "TCS", "2026-11-26", Decimal("105"),
            Decimal("100"), Decimal("2"), Decimal("2"), "mock"
        )
        pool = FakePool()
        with patch("dhan_visual.screener_ui.available_otm_calls",
                   return_value={"TCS": otm}), \
             patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.straddle_panel.scan_all_straddles") as scan:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            screen = app.dataframe[0].value
            self.assertEqual(
                screen.loc[screen["Symbol"] == "TCS", "5%+ CE premium (%)"].iloc[0],
                2.0,
            )
            self.assertTrue(
                screen.loc[screen["Symbol"] == "SBIN", "5%+ CE premium (%)"].isna().all()
            )
            scan.assert_not_called()

    def test_dec_ce_populates_from_locally_downloaded_nse_report(self):
        """Read only a local public UDiFF ZIP, no broker or NSE scraping."""
        from tempfile import TemporaryDirectory
        from dhan_visual.ce_eod_local import recent_nse_fo_reports
        from test_ce_eod_local import save_nse_zip
        with TemporaryDirectory() as folder:
            file = save_nse_zip(folder, "20261123")
            self.assertEqual(
                recent_nse_fo_reports(Path(folder), today=__import__("datetime").date(2026, 11, 25)),
                [file],
            )
            app = app_with_catalogue()
            app.session_state["fno_expiry"] = "DEC-26"
            with patch("dhan_visual.screener_ui.recent_nse_fo_reports", return_value=[file]), \
                 patch("dhan_visual.screener_ui._market_pool", return_value=FakePool()), \
                 patch("dhan_visual.straddle_panel.scan_all_straddles") as scan:
                app.run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                df = app.dataframe[0].value
                value = df.loc[df["Symbol"] == "TCS", "5%+ CE premium (%)"].iloc[0]
                source = df.loc[df["Symbol"] == "TCS", "CE source"].iloc[0]
                self.assertEqual(value, 2.0)
                self.assertEqual(source, "NSE EOD 2026-11-23")
                self.assertTrue(df.loc[df["Symbol"] == "SBIN",
                                       "5%+ CE premium (%)"].isna().all())
                scan.assert_not_called()

    def test_eod_default_is_explicit_and_live_never_uses_eod_close(self):
        """The EOD/Live switch must not silently mix the two prices."""
        from tempfile import TemporaryDirectory
        from test_ce_eod_local import save_nse_zip
        with TemporaryDirectory() as folder:
            file = save_nse_zip(folder, "20261123")
            app = app_with_catalogue()
            app.session_state["fno_expiry"] = "DEC-26"
            with patch("dhan_visual.screener_ui.recent_nse_fo_reports", return_value=[file]), \
                 patch("dhan_visual.screener_ui._market_pool", return_value=FakePool()), \
                 patch("dhan_visual.straddle_panel.scan_all_straddles") as scan:
                app.run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                self.assertEqual(app.radio(key="fno_ce5_mode").value, "EOD")
                frame = app.dataframe[0].value
                self.assertEqual(frame.loc[frame["Symbol"] == "TCS",
                                           "5%+ CE premium (%)"].iloc[0], 2.0)
                app.radio(key="fno_ce5_mode").set_value("Live").run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                frame = app.dataframe[0].value
                self.assertTrue(frame["5%+ CE premium (%)"].isna().all())
                app.radio(key="fno_ce5_mode").set_value("EOD").run()
                self.assertFalse(app.exception, [str(x.message) for x in app.exception])
                self.assertEqual(app.dataframe[0].value.loc[
                    app.dataframe[0].value["Symbol"] == "TCS",
                    "5%+ CE premium (%)"
                ].iloc[0], 2.0)
                scan.assert_not_called()

    def test_tejhq_optional_eod_is_quiet_without_key(self):
        """No paid-provider calls on launch; user controls paid usage."""
        app = app_with_catalogue()
        with patch("dhan_visual.screener_ui._market_pool", return_value=FakePool()), \
             patch("dhan_visual.screener_ui.scan_tej_eod") as paid:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "EOD")
            self.assertIsNotNone(app.button(key="fno_tej_eod_scan"))
            self.assertTrue(app.button(key="fno_tej_eod_scan").disabled)
            paid.assert_not_called()

    def test_explicit_tejhq_eod_snapshot_populates_column_without_live_crossmix(self):
        """A real-shaped paid EOD snapshot is never labelled as Dhan LIVE."""
        from dhan_visual.ce_tejhq import TejEodCall
        app = app_with_catalogue()
        app.session_state["fno_expiry"] = "DEC-26"
        q = TejEodCall(
            symbol="TCS", expiry="2026-12-29",
            strike=Decimal("105"), underlying_close=Decimal("100"),
            call_close=Decimal("2"), premium_pct=Decimal("2"),
            report_date="2026-11-23", volume=12,
        )
        app.session_state["fno_tej_eod_state"] = {
            "month": "DEC-26", "quotes": {"TCS": q}, "errors": {},
            "asof": "2026-11-23", "processed": 1,
            "total": 2, "status": "Completed",
        }
        with patch("dhan_visual.screener_ui.recent_nse_fo_reports",
                   return_value=[]), \
             patch("dhan_visual.screener_ui._market_pool",
                   return_value=FakePool()), \
             patch("dhan_visual.screener_ui.scan_tej_eod") as paid:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            df = app.dataframe[0].value
            tcs = df.loc[df["Symbol"] == "TCS"].iloc[0]
            self.assertEqual(tcs["5%+ CE premium (%)"], 2.0)
            self.assertEqual(tcs["CE source"], "TejHQ EOD 2026-11-23")
            app.radio(key="fno_ce5_mode").set_value("Live").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertTrue(app.dataframe[0].value["5%+ CE premium (%)"].isna().all())
            paid.assert_not_called()

    def test_compact_search_and_filters_retain_all_prior_actions(self):
        app = app_with_catalogue()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            app.text_input(key="fno_symbol_find").set_value("TCS").run()
            self.assertEqual(app.dataframe[0].value["Symbol"].tolist(), ["TCS"])
            app.text_input(key="fno_symbol_find").set_value("").run()
            app.radio(key="fno_value_sort").set_value("High → low").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="fno_value_sort").value, "High → low")
            app.selectbox(key="fno_value_preset").set_value("All stocks").run()
            self.assertEqual(len(app.dataframe[0].value), 2)

    def test_dark_mode_button_toggles_without_broker_calls(self):
        app = app_with_catalogue()
        pool = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=pool), \
             patch("dhan_visual.straddle_panel.scan_all_straddles") as scan:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertFalse(app.session_state["ccs_dark_mode"])
            app.button(key="ccs_theme_button").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertTrue(app.session_state["ccs_dark_mode"])
            app.button(key="ccs_theme_button").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertFalse(app.session_state["ccs_dark_mode"])
            scan.assert_not_called()

    def test_no_manual_list_upload_or_50_stock_hard_cap(self):
        source = (APP.parent / "screener_ui.py").read_text(encoding="utf-8")
        # No manual stock-list upload: the optional uploader is ONLY for
        # a user-provided NSE daily derivatives (option premium) report.
        list_loader = source.split("def _auto_universe():", 1)[1].split(
            "@st.cache_resource\ndef _market_pool()", 1
        )[0]
        self.assertNotIn("file_uploader", list_loader)
        self.assertEqual(source.count("st.file_uploader("), 1)
        self.assertIn("Optional: choose NSE F&O daily ZIP or CSV", source)
        self.assertNotIn("First 50 stocks", source)
        self.assertIn("load_fast_catalogue", source)
        self.assertIn("load_yahoo_all", source)


if __name__ == "__main__":
    unittest.main()
