"""Offline one-stock search: isolated quote requests and detail actions."""
import unittest
from decimal import Decimal
from unittest.mock import patch
from dhan_visual import stock_search_ui as ui
from dhan_visual.screener import Underlying, Quote
from test_selective_scan import access, call, straddle, levels
from test_dhan_studio_startup import app_with_catalogue

def prices():
    return {"SPOT":Quote(Decimal("1000"),"offline","FYERS"),
            "DEC-26":Quote(Decimal("990"),"offline","FYERS")}

class StockSearchTests(unittest.TestCase):
    def test_selected_stock_details_and_no_retry(self):
        entry=Underlying("TCS","TCS",{"DEC-26":1000})
        with patch.object(ui,"fetch_strategy_quotes",return_value=prices()) as quote, \
             patch.object(ui,"resolve_expiry_timestamp",return_value=("123","2026-12-29")) as expiry, \
             patch.object(ui,"fetch_chain",return_value=(call(),straddle())) as chain, \
             patch.object(ui,"fetch_daily_levels",return_value=levels()), \
             patch.object(ui,"fetch_r1_intraday_bars",return_value=object()), \
             patch.object(ui,"session_r1_cross",return_value=(None,Decimal("1000"))):
            data=ui.load_stock_details(access(),entry,"DEC-26")
            row=ui.detail_fields(entry,data)
            self.assertEqual(row["Contract size (₹)"],Decimal("1000000"))
            self.assertEqual(row["Futures vs CMP (%)"],Decimal("-1"))
            self.assertEqual(row["CE strike (₹)"],Decimal("1050"))
            self.assertEqual(row["ATM straddle (%)"],Decimal("7.5"))
            self.assertEqual(row["ATM straddle (₹)"],Decimal("75"))
            self.assertEqual(row["R1 crossed today"],"No")
            self.assertEqual(row["Above Monthly R1"],"Yes")
            quote.assert_called_once()
            self.assertEqual(quote.call_args.args[1],"TCS")
            self.assertEqual(expiry.call_args.kwargs["probe"],"TCS")
            self.assertEqual(chain.call_args.args[1],"TCS")
        with patch.object(ui,"fetch_strategy_quotes",side_effect=ui.FyersDataError("rate limit")) as q, \
             patch.object(ui,"fetch_chain") as chain:
            data=ui.load_stock_details(access(),entry,"DEC-26")
            self.assertEqual(data["error"],"rate limit")
            q.assert_called_once()
            chain.assert_not_called()
            self.assertIsNone(ui.detail_fields(entry,data)["CMP (₹)"])

    def test_home_search_is_lazy_and_independent(self):
        app=app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"]="FYERS"
        app.session_state["_fyers_access"]=access()
        app.session_state["ccs_phone_layout"]=True
        with patch("dhan_visual.selective_scan.fetch_cash_quotes") as market, \
             patch.object(ui,"fetch_strategy_quotes",return_value=prices()) as q, \
             patch.object(ui,"resolve_expiry_timestamp",return_value=("123","2026-12-29")), \
             patch.object(ui,"fetch_chain",return_value=(call(),straddle())), \
             patch.object(ui,"fetch_daily_levels",return_value=levels()), \
             patch.object(ui,"fetch_r1_intraday_bars",return_value=object()), \
             patch.object(ui,"session_r1_cross",return_value=(None,Decimal("1000"))):
            app.run()
            app.button(key="ccs_open_stock_search").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            q.assert_not_called()
            app.selectbox(key="ccs_stock_search_symbol").set_value("TCS").run()
            app.selectbox(key="ccs_stock_search_expiry_TCS").set_value("DEC-26").run()
            q.assert_not_called()
            app.button(key="ccs_stock_search_load").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            q.assert_called_once()
            self.assertEqual(app.session_state[ui.RESULT]["data"]["symbol"],"TCS")
            app.run()
            q.assert_called_once()
            market.assert_not_called()
            app.button(key="ccs_stock_search_back").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            market.assert_not_called()

if __name__=="__main__":
    unittest.main()
