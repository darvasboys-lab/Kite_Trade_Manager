"""Offline v1.0 persistence and usability tests; no broker connections."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dhan_visual import workspace_store as store
from dhan_visual.stock_strategy import Leg, payoff, payoff_summary
from dhan_visual.api_monitor import record, stats
from test_dhan_studio_startup import app_with_catalogue
from test_selective_scan import access


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env=patch.dict("os.environ",{"CCS_DATA_DIR":self.temp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_preferences_presets_snapshot_backup_roundtrip(self):
        f=dict(maximum=7,minimum=2,expiry="DEC-26",r1_filter="Include all stocks")
        data=store.read()
        data["remember"]=True
        data["preferences"]={"dark":False,"autofit":True,"filters":f}
        data["presets"]["Covered Call"]=f
        data["strategies"]["Example"]=dict(symbol="TCS",expiry="DEC-26",charges=50,
            legs=[dict(kind="CE",side="Sell",expiry="DEC-26",strike=1050,units=100,price=25)])
        data["snapshot"]=dict(saved_at="2026-11-25T10:00:00+05:30",filters=f,
            rows=[{"Stock":"TCS","CMP (₹)":1000,"> R1":"✅ Yes","ATM Straddle Total (₹)":None}])
        store.write(data)
        self.assertEqual(store.read(),data)
        old=store.location().read_bytes()
        data["strategies"]["Example"]["legs"][0]["units"]=-1
        with self.assertRaises(ValueError):
            store.write(data)
        self.assertEqual(store.location().read_bytes(),old)

    def test_unknown_secret_fields_are_not_persisted(self):
        data=store.read()
        data["token"]="SENSITIVE_TEST_VALUE"
        data["preferences"]["password"]="SENSITIVE_TEST_VALUE"
        store.write(data)
        self.assertNotIn("SENSITIVE_TEST_VALUE",store.location().read_text())

    def test_charges_adjust_exact_payoff(self):
        legs=[Leg("Equity","Buy","",0,100,1000),Leg("CE","Sell","DEC-26",1050,100,25)]
        self.assertEqual(payoff(legs,1050,100),7400)
        result=payoff_summary(legs,100)
        self.assertEqual(result["max_profit"],7400)
        self.assertEqual(result["max_loss"],97600)
        self.assertEqual(result["breakevens"],[976])

    def test_api_counter_increments(self):
        before=stats()["counts"].get(("FYERS","request"),0)
        record("FYERS")
        self.assertEqual(stats()["counts"][("FYERS","request")],before+1)

    def test_independent_stock_search_does_not_request_quotes(self):
        app=app_with_catalogue(legacy=False)
        app.session_state["ccs_active_broker"]="FYERS"
        app.session_state["_fyers_access"]=access()
        with patch("dhan_visual.stock_strategy_ui.fetch_strategy_expiries") as dates, \
             patch("dhan_visual.stock_strategy_ui.fetch_strategy_quotes") as quotes, \
             patch("dhan_visual.selective_scan.fetch_cash_quotes") as cash:
            app.run()
            app.radio(key="ccs_selective_page").set_value("◇ Strategy Builder").run()
            self.assertFalse(app.exception,[str(x.message) for x in app.exception])
            dates.assert_not_called()
            quotes.assert_not_called()
            cash.assert_not_called()
            self.assertTrue(app.selectbox(key="ccs_independent_symbol").options)


if __name__=="__main__":
    unittest.main()
