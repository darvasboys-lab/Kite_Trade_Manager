"""Futures discount view: independent of option filters, bounded and manual."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from dhan_visual.screener import Quote, Underlying
from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.selective_scan import futures_discount_frame, load_futures_discount
from test_selective_scan import access
from test_dhan_studio_startup import app_with_catalogue


def fixture():
    names = ["AAA","BBB","CCC","DDD","EEE","FFF"]
    lots = [100,100,100,100,700,100]
    universe = [DhanUnderlying(Underlying(s,s,{"DEC-26":n}),i+1)
                for i,(s,n) in enumerate(zip(names,lots))]
    cash = {s:Quote(Decimal("1000"),"offline","FYERS") for s in names[:-1]}
    futures = {s:Quote(Decimal(p),"offline","FYERS futures")
               for s,p in [("AAA","1010"),("BBB","980"),("CCC","995"),("EEE","900")]}
    return universe,cash,futures


class FuturesDiscountTests(unittest.TestCase):
    def test_strict_cap_signed_numeric_sort_and_missing_last(self):
        universe,cash,futures=fixture()
        result=futures_discount_frame(universe,cash,futures,"DEC-26")
        self.assertEqual(result.Stock.tolist(),["BBB","CCC","AAA","DDD"])
        self.assertEqual(result["Futures vs CMP (%)"].iloc[:3].tolist(),[-2.,-.5,1.])
        self.assertTrue(result["Futures vs CMP (%)"].isna().iloc[-1])
        self.assertTrue((result["Contract size (₹)"]<700000).all())

    def test_full_cash_universe_then_all_qualified_futures(self):
        universe,cash,futures=fixture()
        with patch("dhan_visual.selective_scan.fetch_cash_quotes",return_value=cash) as spot, \
             patch("dhan_visual.selective_scan.fetch_futures_quotes",return_value=futures) as future, \
             patch("dhan_visual.selective_scan.fetch_chain") as chain:
            result,total,priced=load_futures_discount(access(),universe,"DEC-26")
            self.assertEqual((total,priced),(6,5))
            self.assertEqual(spot.call_args.args[1],["AAA","BBB","CCC","DDD","EEE","FFF"])
            self.assertEqual(future.call_args.args[1],["AAA","BBB","CCC","DDD"])
            self.assertEqual(len(result),4)
            chain.assert_not_called()

    def test_futures_button_before_any_option_scan_ignores_filters_and_caches(self):
        app=app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"]="FYERS"
        app.session_state["_fyers_access"]=access()
        cash={s:Quote(Decimal("1000"),"offline","FYERS") for s in ("TCS","SBIN")}
        futures={"TCS":Quote(Decimal("980"),"offline","FYERS futures")}
        with patch("dhan_visual.selective_scan.fetch_cash_quotes",return_value=cash) as spot, \
             patch("dhan_visual.selective_scan.fetch_futures_quotes",return_value=futures) as future, \
             patch("dhan_visual.selective_scan.fetch_chain") as chain, \
             patch("dhan_visual.selective_scan.fetch_daily_levels") as history:
            app.run()
            spot.assert_not_called()
            app.radio(key="selective_expiry_choice").set_value("DEC-26")
            app.radio(key="selective_contract_choice").set_value(4)
            app.radio(key="selective_premium_choice").set_value(8)
            app.radio(key="selective_r1_choice").set_value("Above R1 only")
            next(x for x in app.button if x.label=="Show futures · < ₹7L").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            self.assertEqual(app.dataframe[0].value.Stock.tolist(),["TCS"])
            self.assertEqual(app.dataframe[0].value["Futures vs CMP (%)"].tolist(),[-2.])
            spot.assert_called_once()
            future.assert_called_once()
            app.run()
            spot.assert_called_once()
            future.assert_called_once()
            chain.assert_not_called()
            history.assert_not_called()
            app.button(key="ccs_futures_hide").click().run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            spot.assert_called_once()
            future.assert_called_once()
            chain.assert_not_called()


if __name__=="__main__":
    unittest.main()
