"""Offline strategy-chain, payoff, and lazy workspace tests."""
import unittest
from decimal import Decimal
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from dhan_visual.stock_strategy import Leg, payoff, payoff_summary, expiry_legs
from dhan_visual.fyers_readonly import parse_strategy_chain, FyersDataError, FyersAccess
from dhan_visual.stock_strategy_ui import _preset, _chain_table
from test_selective_scan import access, quotes, call, straddle, levels
from test_dhan_studio_startup import app_with_catalogue


def chain():
    return parse_strategy_chain({"data":{"optionsChain":[
        {"symbol":"NSE:TCS26DEC1000CE","strike_price":1000,"option_type":"CE","ltp":40},
        {"symbol":"NSE:TCS26DEC1000PE","strike_price":1000,"option_type":"PE","ltp":35},
        {"symbol":"NSE:TCS26DEC1050CE","strike_price":1050,"option_type":"CE","ltp":25},
        {"symbol":"NSE:TCS27JAN1000CE","strike_price":1000,"option_type":"CE","ltp":99},
        {"symbol":"NSE:TCSS26DEC1000PE","strike_price":1000,"option_type":"PE","ltp":99},
    ]}},"TCS","DEC-26")


class StockStrategyTests(unittest.TestCase):
    def test_month_and_symbol_verified_and_missing_quotes_not_invented(self):
        data = chain()
        self.assertEqual(len(data),3)
        self.assertEqual(data[(Decimal(1000),"CE")]["ltp"], Decimal(40))
        self.assertEqual(len(_chain_table(data,1000)),2)
        self.assertIsNone(data[(Decimal(1000),"CE")]["bid"])
        with self.assertRaises(FyersDataError):
            parse_strategy_chain({"data":{"optionsChain":[]}},"TCS","DEC-26")

    def test_covered_call_and_straddle_exact_risk(self):
        cc = _preset("Covered call",chain(),1000,"DEC-26",100)
        stats = payoff_summary(cc)
        self.assertEqual(stats["max_profit"],7500)
        self.assertEqual(stats["max_loss"],97500)
        self.assertEqual(stats["breakevens"],[975])
        ss = _preset("Short straddle",chain(),1000,"DEC-26",100)
        stats = payoff_summary(ss)
        self.assertEqual(stats["max_profit"],7500)
        self.assertEqual(stats["max_loss"],float("inf"))
        self.assertEqual(stats["breakevens"],[925,1075])
        self.assertEqual(payoff(ss,1000),7500)
        self.assertEqual(expiry_legs(ss,"JAN-27"),[])
        self.assertEqual(payoff_summary([Leg("PE","Buy","DEC-26",1000,100,35)])["max_loss"],3500)

    def test_strategy_only_loads_after_explicit_click_and_reuses_cache(self):
        app = app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"]="FYERS"
        app.session_state["_fyers_access"]=access()
        with patch("dhan_visual.selective_scan.fetch_cash_quotes",return_value=quotes()), \
             patch("dhan_visual.selective_scan.resolve_expiry_timestamp",return_value=("123","2026-12-29")), \
             patch("dhan_visual.selective_scan.fetch_chain",return_value=(call(),straddle())), \
             patch("dhan_visual.selective_scan.fetch_daily_levels",return_value=levels()), \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_expiries",return_value={"DEC-26":("123","2026-12-29")}) as dates, \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_quotes",return_value={"SPOT":quotes()["TCS"]}) as prices, \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_chain",return_value=chain()) as full:
            app.run()
            dates.assert_not_called()
            prices.assert_not_called()
            full.assert_not_called()
            app.radio(key="selective_expiry_choice").set_value("DEC-26")
            app.radio(key="selective_contract_choice").set_value(4)
            next(x for x in app.button if x.label=="Apply filters / Start scan").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            full.assert_not_called()
            app.button(key="ccs_strategy_from_straddle").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            dates.assert_called_once()
            prices.assert_called_once()
            full.assert_called_once()
            self.assertFalse(app.session_state["ccs_selective_scan"]["auto_scan"])
            app.run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            full.assert_called_once()
            app.button(key="ccs_strategy_back").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            full.assert_called_once()


    def test_phone_cards_open_selected_stock_strategy_without_extra_scan(self):
        app=app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"]="FYERS"
        app.session_state["_fyers_access"]=access()
        app.session_state["ccs_phone_layout"]=True
        with patch("dhan_visual.selective_scan.fetch_cash_quotes",return_value=quotes()), \
             patch("dhan_visual.selective_scan.resolve_expiry_timestamp",return_value=("123","2026-12-29")), \
             patch("dhan_visual.selective_scan.fetch_chain",return_value=(call(),straddle())) as scan, \
             patch("dhan_visual.selective_scan.fetch_daily_levels",return_value=levels()), \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_expiries",return_value={"DEC-26":("123","2026-12-29")}), \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_quotes",return_value={"SPOT":quotes()["TCS"]}), \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_chain",return_value=chain()) as full:
            app.run()
            app.radio(key="selective_expiry_choice").set_value("DEC-26")
            app.radio(key="selective_contract_choice").set_value(4)
            next(x for x in app.button if x.label=="Apply filters / Start scan").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_results_layout_home").value,"Mobile cards")
            self.assertEqual(len(app.dataframe),0)
            scan.assert_called_once()
            next(x for x in app.button if x.key and x.key.startswith("ccs_card_TCS_DEC-26_Straddle")).click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(app.session_state["ccs_stock_strategy_active"]["symbol"],"TCS")
            self.assertEqual(app.session_state["ccs_stock_strategy_active"]["preset"],"Short straddle")
            full.assert_called_once()
            scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
