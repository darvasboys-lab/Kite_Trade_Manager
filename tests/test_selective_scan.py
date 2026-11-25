"""Regression tests for default request-on-demand workspace."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from dhan_visual.fyers_readonly import FyersAccess, FyersCall, FyersStraddle
from dhan_visual.screener import Quote
from dhan_visual.selective_scan import (
    new_snapshot, contract_candidates, qualified_rows, next_option_batch,
)
from test_dhan_studio_startup import app_with_catalogue, example


def access():
    return FyersAccess("ABCD1234-100", "offline-fixture-not-real",
                       datetime.now(timezone.utc) + timedelta(hours=8))


def quotes():
    return {s: Quote(Decimal("1000"), "offline", "FYERS") for s in ("TCS", "SBIN")}


def call(symbol="TCS", pct="3"):
    return FyersCall(symbol, "DEC-26", Decimal("1050"), Decimal("1000"),
                     Decimal(pct)*10, Decimal(pct))


def straddle():
    return FyersStraddle("TCS", "DEC-26", Decimal("1000"), Decimal("1000"),
                         Decimal("40"), Decimal("35"), Decimal("7.5"))


class SelectiveScanTests(unittest.TestCase):
    def test_contract_first_then_strict_ce_threshold_and_cache_reuse(self):
        snap = new_snapshot("key", "DEC-26", 7, 2)
        snap["cash"] = quotes()
        self.assertEqual(contract_candidates(example(), snap).Stock.tolist(), ["TCS"])
        with patch("dhan_visual.selective_scan.resolve_expiry_timestamp", return_value=("123", "2026-12-29")) as resolve, \
             patch("dhan_visual.selective_scan.fetch_chain", return_value=(call(), straddle())) as fetch:
            self.assertEqual(next_option_batch(access(), example(), snap), 1)
            self.assertEqual(fetch.call_args.args[1], "TCS")
            self.assertEqual(qualified_rows(example(), snap).Stock.tolist(), ["TCS"])
            snap["minimum"] = 3
            self.assertTrue(qualified_rows(example(), snap).empty)
            self.assertEqual(next_option_batch(access(), example(), snap), 0)
            fetch.assert_called_once()
            resolve.assert_called_once()
            snap["maximum"] = 8
            snap["minimum"] = 2
            fetch.return_value = (call("SBIN"), None)
            self.assertEqual(next_option_batch(access(), example(), snap), 1)
            self.assertEqual(fetch.call_args.args[1], "SBIN")
            resolve.assert_called_once()

    def test_stop_on_provider_error_without_retry(self):
        from dhan_visual.fyers_readonly import FyersDataError
        snap = new_snapshot("key", "DEC-26", 8, 2)
        snap["cash"] = quotes()
        with patch("dhan_visual.selective_scan.resolve_expiry_timestamp", return_value=("123", "2026-12-29")), \
             patch("dhan_visual.selective_scan.fetch_chain", side_effect=FyersDataError("rate limit")) as fetch:
            next_option_batch(access(), example(), snap)
            self.assertTrue(snap["stopped"])
            self.assertEqual(next_option_batch(access(), example(), snap), 0)
            fetch.assert_called_once()

    def test_default_home_and_market_are_lazy_then_use_same_shortlist(self):
        app = app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"] = "FYERS"
        app.session_state["_fyers_access"] = access()
        with patch("dhan_visual.selective_scan.fetch_cash_quotes", return_value=quotes()) as cash, \
             patch("dhan_visual.selective_scan.resolve_expiry_timestamp", return_value=("123", "2026-12-29")), \
             patch("dhan_visual.selective_scan.fetch_chain", return_value=(call(), straddle())) as chain, \
             patch("dhan_visual.selective_scan.fetch_daily_levels") as daily, \
             patch("dhan_visual.selective_scan.fetch_r1_intraday_bars") as intraday, \
             patch("dhan_visual.screener_ui.render_screener") as legacy:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertFalse(app.toggle(key="ccs_legacy_full_scan").value)
            cash.assert_not_called()
            chain.assert_not_called()
            daily.assert_not_called()
            intraday.assert_not_called()
            legacy.assert_not_called()
            app.radio(key="ccs_selective_page").set_value("⌕ Market Screener").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            cash.assert_not_called()
            chain.assert_not_called()
            daily.assert_not_called()
            app.radio(key="ccs_selective_page").set_value("◎ OTM CE Home").run()
            self.assertEqual(app.radio(key="selective_contract_choice").value, 7)
            self.assertEqual(app.radio(key="selective_premium_choice").value, 2)
            app.radio(key="selective_expiry_choice").set_value("DEC-26")
            app.radio(key="selective_contract_choice").set_value(4)
            app.radio(key="selective_premium_choice").set_value(2)
            cash.assert_not_called()
            chain.assert_not_called()
            next(x for x in app.button if x.label == "Apply filters / Start scan").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            cash.assert_called_once()
            chain.assert_called_once()
            self.assertEqual(chain.call_args.args[1], "TCS")
            self.assertEqual(app.dataframe[0].value.Stock.tolist(), ["TCS"])
            app.run()
            cash.assert_called_once()
            chain.assert_called_once()
            app.radio(key="ccs_selective_page").set_value("⌕ Market Screener").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            cash.assert_called_once()
            chain.assert_called_once()
            daily.assert_not_called()
            intraday.assert_not_called()
            legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
