import json
import threading
import unittest
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

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
