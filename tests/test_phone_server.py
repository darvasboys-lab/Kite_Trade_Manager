import json
import threading
import unittest
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch, Mock
from datetime import datetime
from types import SimpleNamespace

from dhan_visual import phone_server as phone


class PhoneCandidateTests(unittest.TestCase):
    def inputs(self):
        return dict(avg=1000,shares=100,strike=1200,lot=500,credit=10000,
                    tick=0.05,fees=0,batches=3,spot=1100,ask=18,done=0,exit_fees=0)

    def test_uses_existing_strategy(self):
        result=phone.strategy(self.inputs())
        self.assertEqual(result["owned"],100)
        self.assertEqual(result["uncovered"],400)
        self.assertEqual(result["plan"]["final_trigger"],Decimal("1194.00"))
        self.assertEqual(sum(x["shares"] for x in result["plan"]["stages"]),400)
        self.assertEqual(result["plan"]["profit_target"],
                         result["plan"]["projected_profit_at_strike"]/2)
        self.assertEqual(len(result["curve"]),81)

    def test_reject_fractional_shares(self):
        with self.assertRaises(ValueError):
            phone.strategy({**self.inputs(),"shares":100.5})

    def test_scan_requires_connection(self):
        with patch.dict(phone.STATE,{"vault":None}):
            with self.assertRaises(ValueError):
                phone.dispatch("/scan",{})

    def test_default_scan_filters_and_cooldown(self):
        with patch.object(phone,"access",return_value=object()), patch.object(phone,"months",return_value=["DEC-26"]), patch.object(phone,"LAST_BATCH",100), patch.object(phone.time,"monotonic",return_value=110), patch.dict(phone.STATE,{"scan":None}):
            result=phone.dispatch("/scan",{})
            self.assertEqual(result["cooldown"],50)
            self.assertFalse(result["pending"])

    def test_reuse_matching_snapshot_without_new_broker_requests(self):
        day=datetime.now(phone.IST).date().isoformat()
        snap=phone.new_snapshot("phone","DEC-26",7,2,"Include all stocks")
        snap["scan_day"]=day
        with patch.object(phone,"access",return_value=object()), patch.object(phone,"months",return_value=["DEC-26"]), patch.dict(phone.STATE,{"scan":snap}), patch.object(phone,"scan_result",return_value={"rows":[],"pending":True}) as result, patch.object(phone,"fetch_cash_quotes") as quotes:
            self.assertTrue(phone.dispatch("/scan",{})["pending"])
            result.assert_called_once()
            quotes.assert_not_called()

    def test_expiry_defaults_skip_older_months_and_expired_fyers_month(self):
        today=datetime(2026,11,27,tzinfo=phone.IST)
        entries=[SimpleNamespace(entry=SimpleNamespace(symbol="SBIN",lots={"OCT-26":500,"NOV-26":500,"DEC-26":500,"JAN-27":500}))]
        vault=Mock()
        vault.access.return_value=SimpleNamespace(app_id="test",expires_at=today)
        with patch.object(phone,"datetime") as clock, patch.dict(phone.STATE,{"vault":vault,"catalogue_day":today.date(),"verified_months":None}), patch.object(phone,"catalogue",return_value=entries), patch.object(phone,"fetch_strategy_expiries",return_value={"DEC-26":("1","2026-12-29"),"JAN-27":("2","2027-01-26")}) as verify:
            clock.now.return_value=today
            result=phone.dispatch("/months",{})
            self.assertEqual(result,{"months":["DEC-26","JAN-27"],"default":"DEC-26"})
            phone.dispatch("/months",{})
            verify.assert_called_once()

    def test_connection_error_is_specific(self):
        with patch.dict(phone.STATE,{"vault":None}):
            with self.assertRaisesRegex(phone.PhoneInputError,"Unlock and authorize FYERS"):
                phone.dispatch("/scan",{})

    def test_unknown_order_route(self):
        with self.assertRaises(ValueError):
            phone.dispatch("/orders",{})

    def test_static_root_and_cross_origin_rejection(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),phone.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            port=server.server_address[1]
            with patch.object(phone,"HOST",f"127.0.0.1:{port}"), patch.object(phone,"ORIGIN",f"http://127.0.0.1:{port}"):
                conn=HTTPConnection("127.0.0.1",port,timeout=5)
                try:
                    conn.request("GET","/")
                    r=conn.getresponse()
                    self.assertEqual(r.status,200)
                    html=r.read().decode()
                    self.assertIn("CCS Terminal",html)
                    self.assertNotIn("__CCS_TOKEN__",html)
                    self.assertIn("no-store",r.getheader("Cache-Control"))
                    conn.request("POST","/status","{}",{"Origin":"https://example.com","Content-Type":"application/json","X-CCS-Token":phone.TOKEN})
                    r=conn.getresponse()
                    self.assertEqual(r.status,403)
                    r.read()
                    conn.request("POST","/strategy",json.dumps(self.inputs()),{"Origin":phone.ORIGIN,"Content-Type":"application/json","X-CCS-Token":phone.TOKEN})
                    r=conn.getresponse()
                    self.assertEqual(r.status,200)
                    self.assertEqual(json.loads(r.read())["owned"],100)
                finally:
                    conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

if __name__=="__main__":
    unittest.main()
