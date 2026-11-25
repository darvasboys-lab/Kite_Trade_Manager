"""FYERS-first options and genuine Home ATM CE+PE tests (offline only)."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from dhan_visual.fyers_readonly import FyersAccess, FyersCall, FyersStraddle
from dhan_visual.otm_home import otm_home_dataframe, filter_home_contracts
from dhan_visual.screener import Quote
from test_dhan_studio_startup import app_with_catalogue, example, FakePool


def valid_access():
    return FyersAccess(
        "ABCD1234-100", "offline-test-fyers-token-not-real",
        datetime.now(timezone.utc) + timedelta(hours=8),
    )


def fyers_call():
    return FyersCall(
        "TCS", "DEC-26", Decimal("105"), Decimal("100"),
        Decimal("2"), Decimal("2"),
    )


def fyers_straddle(expiry="DEC-26"):
    return FyersStraddle(
        "TCS", expiry, Decimal("100"), Decimal("100"),
        Decimal("5"), Decimal("7"), Decimal("12"),
    )


class PreferredFYERSHomeTests(unittest.TestCase):
    def test_futures_quote_only_accepts_exact_requested_contracts(self):
        from dhan_visual.fyers_readonly import fetch_futures_quotes
        payload = {"d": [
            {"s": "ok", "n": "NSE:TCS26DECFUT", "v": {"lp": 1005}},
            {"s": "ok", "n": "NSE:TCS-EQ", "v": {"lp": 1}},
            {"s": "ok", "n": "NSE:SBIN26NOVFUT", "v": {"lp": 2}},
            {"s": "ok", "n": "NSE:SBIN26DECFUT", "v": {"lp": 0}},
        ]}
        with patch("dhan_visual.fyers_readonly._get", return_value=payload) as get:
            quotes = fetch_futures_quotes(valid_access(), ["TCS", "SBIN"], "DEC-26")
        self.assertEqual(set(quotes), {"TCS"})
        self.assertEqual(quotes["TCS"].price, Decimal("1005"))
        self.assertEqual(get.call_args.args[1], "/data/quotes")
        self.assertEqual(get.call_args.args[2]["symbols"], "NSE:TCS26DECFUT,NSE:SBIN26DECFUT")

    def test_home_futures_are_manual_and_expiry_scoped(self):
        app = app_with_catalogue()
        app.session_state["fno_home_expiry"] = "DEC-26"
        app.session_state["fno_expiry"] = "DEC-26"
        app.session_state["_fyers_access"] = valid_access()
        app.session_state["ccs_active_broker"] = "FYERS"
        app.session_state["fno_ce5_mode"] = "FYERS"
        app.session_state["fno_home_ce_source"] = "FYERS"
        app.session_state["fno_price_provider"] = "FYERS"
        pool = FakePool()
        with patch("dhan_visual.fyers_ui._fyers_pool", return_value=pool), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None), \
             patch("dhan_visual.otm_home.fetch_futures_quotes", return_value={
                 "TCS": Quote(Decimal("1005"), "offline", "FYERS futures LTP"),
             }) as fetch:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            fetch.assert_not_called()
            self.assertNotIn("Futures Price (₹)", app.dataframe[0].value.columns)
            self.assertNotIn("Futures vs CMP (%)", app.dataframe[0].value.columns)
            app.button(key="fno_home_refresh_futures").click().run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            fetch.assert_called_once()
            home = app.dataframe[0].value
            self.assertEqual(home.loc[home.Stock == "TCS", "Futures Price (₹)"].iloc[0], 1005)
            self.assertIn("Futures vs CMP (%)", home.columns)
            app.run()
            fetch.assert_called_once()
            app.button(key="fno_home_refresh_futures").click().run()
            self.assertNotIn("Futures Price (₹)", app.dataframe[0].value.columns)
            self.assertNotIn("Futures vs CMP (%)", app.dataframe[0].value.columns)
            fetch.assert_called_once()
            app.button(key="fno_home_refresh_futures").click().run()
            self.assertEqual(fetch.call_count, 2)
            app.selectbox(key="fno_home_expiry").set_value("NOV-26").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn("Futures Price (₹)", app.dataframe[0].value.columns)
            self.assertNotIn("Futures vs CMP (%)", app.dataframe[0].value.columns)


    def test_signed_futures_basis(self):
        import pandas as pd
        from dhan_visual.otm_home import attach_futures_columns
        frame = pd.DataFrame({"Stock": ["A", "B", "C", "D", "E", "F"],
                              "CMP (₹)": [1000, 1000, 1000, 0, None, 1000]})
        quotes = {
            s: Quote(Decimal(p), "offline", "FYERS futures LTP")
            for s, p in (("A", "1010"), ("B", "980"), ("C", "1000"),
                         ("D", "1000"), ("E", "1000"))
        }
        data = attach_futures_columns(frame, quotes)
        self.assertEqual(data["Futures vs CMP (%)"].iloc[:3].tolist(), [1.0, -2.0, 0.0])
        self.assertTrue(data["Futures vs CMP (%)"].iloc[3:].isna().all())
        self.assertTrue(pd.isna(data["Futures Price (₹)"].iloc[5]))
        self.assertNotIn("Futures Price (₹)", frame.columns)

    def test_contract_size_and_straddle_percent(self):
        from dataclasses import replace
        atm = replace(fyers_straddle(), call_ltp=Decimal("40"), put_ltp=Decimal("35"))
        data = otm_home_dataframe(
            example(), {
                "TCS": Quote(Decimal("1000"), "offline", "FYERS"),
                "SBIN": Quote(Decimal("1000"), "offline", "FYERS"),
            }, {}, straddles={"TCS": atm}, expiry="DEC-26",
        )
        tcs = data.loc[data.Stock == "TCS"].iloc[0]
        self.assertEqual(tcs["Lot size"], 225)
        self.assertEqual(tcs["Contract size (₹)"], 225000)
        self.assertEqual(tcs["ATM Straddle Total (₹)"], 75)
        self.assertEqual(tcs["Straddle (%)"], 7.5)
        self.assertEqual(filter_home_contracts(data, 7).Stock.tolist(), ["TCS"])
        self.assertEqual(filter_home_contracts(data, 8).Stock.tolist(), ["TCS", "SBIN"])
        self.assertTrue(filter_home_contracts(data, 2.25).empty)
        self.assertEqual(len(filter_home_contracts(data, 0)), 2)

    def test_missing_or_invalid_cmp_is_not_an_eligible_contract(self):
        for value in (None, Decimal("0"), Decimal("-1"), Decimal("NaN")):
            quotes = {} if value is None else {"TCS": Quote(value, "offline", "FYERS")}
            data = otm_home_dataframe(
                example(), quotes, {}, straddles={"TCS": fyers_straddle()}, expiry="DEC-26",
            )
            self.assertTrue(data["Contract size (₹)"].isna().all())
            self.assertTrue(data["Straddle (%)"].isna().all())
            self.assertTrue(filter_home_contracts(data, 7).empty)
            self.assertEqual(len(filter_home_contracts(data, 0)), 2)

    def test_home_filter_defaults_to_seven_lakh(self):
        app = app_with_catalogue(home_limit=None)
        app.run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        self.assertEqual(app.number_input(key="fno_home_contract_max_lakh").value, 7.0)
        app.number_input(key="fno_home_contract_max_lakh").set_value(8.0).run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        self.assertEqual(app.number_input(key="fno_home_contract_max_lakh").value, 8.0)

    def test_ce_plus_pe_is_a_rupee_per_share_total_and_uses_same_expiry(self):
        data = otm_home_dataframe(
            example(), {}, {"TCS": fyers_call()},
            straddles={"TCS": fyers_straddle()}, expiry="DEC-26",
        )
        self.assertEqual(
            data.columns.tolist(),
            ["Stock", "CMP (₹)", "Lot size", "Contract size (₹)", "5%+ OTM CE (%)", "ATM Straddle Total (₹)", "Straddle (%)"],
        )
        self.assertEqual(
            data.loc[data.Stock == "TCS", "ATM Straddle Total (₹)"].iloc[0],
            12.0,
        )
        self.assertEqual(
            data.loc[data.Stock == "TCS", "5%+ OTM CE (%)"].iloc[0],
            2.0,
        )
        self.assertTrue(
            data.loc[data.Stock == "SBIN", "ATM Straddle Total (₹)"].isna().all(),
        )

    def test_missing_or_mismatched_legs_cannot_fabricate_atm_total(self):
        from dataclasses import replace
        for value in (
            fyers_straddle("NOV-26"),
            replace(fyers_straddle(), symbol="INFY"),
            replace(fyers_straddle(), call_ltp=Decimal("0")),
            replace(fyers_straddle(), put_ltp=Decimal("0")),
        ):
            with self.subTest(value=value):
                data = otm_home_dataframe(
                    example(), {}, {"TCS": fyers_call()},
                    straddles={"TCS": value}, expiry="DEC-26",
                )
                self.assertTrue(data["ATM Straddle Total (₹)"].isna().all())
        # EOD has no actual ATM pair; no synthetic total is invented.
        self.assertTrue(
            otm_home_dataframe(example(), {}, {}, expiry="DEC-26")[
                "ATM Straddle Total (₹)"
            ].isna().all()
        )

    def test_saved_fyers_app_default_even_when_access_token_needs_renewal(self):
        app = app_with_catalogue()
        with patch("dhan_visual.broker_control.read_app_from_vault",
                   return_value=("ABCD1234-100", "offline-test-only")), \
             patch("dhan_visual.broker_control.read_access_from_vault",
                   return_value=None), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None), \
             patch("dhan_visual.screener_ui._start_auto_login") as dhan_login, \
             patch("dhan_visual.screener_ui.check_dhan_data_plan") as profile:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "FYERS")
            self.assertEqual(app.radio(key="fno_home_ce_source").value, "FYERS")
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "FYERS")
            self.assertEqual(app.radio(key="fno_price_provider").value, "FYERS")
            dhan_login.assert_not_called()
            profile.assert_not_called()

    def test_explicit_dhan_selection_wins_over_saved_fyers_config(self):
        app = app_with_catalogue()
        app.session_state["ccs_active_broker"] = "Dhan"
        app.session_state["fno_ce5_mode"] = "EOD"
        with patch("dhan_visual.broker_control.read_app_from_vault",
                   return_value=("ABCD1234-100", "offline-test-only")), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "Dhan")
            self.assertEqual(app.radio(key="fno_home_ce_source").value, "EOD")

    def test_home_uses_shared_matching_fyers_option_snapshot_not_old_expiry(self):
        from dhan_visual.fyers_ui import _scope
        app = app_with_catalogue()
        app.session_state["fno_home_expiry"] = "DEC-26"
        app.session_state["fno_expiry"] = "DEC-26"
        app.session_state["_fyers_access"] = valid_access()
        app.session_state["ccs_active_broker"] = "FYERS"
        app.session_state["fno_ce5_mode"] = "FYERS"
        app.session_state["fno_home_ce_source"] = "FYERS"
        app.session_state["fno_price_provider"] = "FYERS"
        app.session_state["fno_fyers_chain_snapshot"] = {
            "key": _scope(valid_access(), ["SBIN", "TCS"], "DEC-26"),
            "expiry": "DEC-26",
            "calls": {"TCS": fyers_call()},
            "straddles": {"TCS": fyers_straddle()},
            "total": 2, "processed": 2, "errors": [],
            "complete": True, "expiry_date": "2026-12-29",
        }
        pool = FakePool()
        with patch("dhan_visual.fyers_ui._fyers_pool", return_value=pool), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            home = app.dataframe[0].value
            self.assertEqual(
                home.loc[home.Stock == "TCS", "ATM Straddle Total (₹)"].iloc[0],
                12.0,
            )
            self.assertEqual(
                home.loc[home.Stock == "TCS", "5%+ OTM CE (%)"].iloc[0],
                2.0,
            )
            self.assertEqual(
                sum(args[0][0].__name__ == "_fyers_chain_worker" for args in pool.calls),
                0, "Matching options snapshot should not refetch a chain",
            )
            # A stale expiry must not appear in the Home straddle column.
            app.session_state["fno_fyers_chain_snapshot"]["expiry"] = "NOV-26"
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            stale = app.dataframe[0].value
            self.assertTrue(stale["ATM Straddle Total (₹)"].isna().all())
            self.assertTrue(stale["5%+ OTM CE (%)"].isna().all())


if __name__ == "__main__":
    unittest.main()
