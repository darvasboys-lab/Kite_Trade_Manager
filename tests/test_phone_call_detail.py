import unittest
from decimal import Decimal
from types import SimpleNamespace
from dhan_visual.phone_call_detail import call_detail


class PhoneCallDetailTests(unittest.TestCase):
    def call(self, **overrides):
        return SimpleNamespace(**{
            "symbol":"EXAMPLE", "expiry":"DEC-26", "strike":Decimal("106"),
            "underlying_ltp":Decimal("100"), "call_ltp":Decimal("3.5"),
            "premium_pct":Decimal("3.50"), "bid":Decimal("3.4"),
            "ask":Decimal("3.6"), "volume":100, **overrides
        })

    def detail(self, call, **overrides):
        return call_detail(call, **{
            "symbol":"EXAMPLE", "expiry":"DEC-26", "premium_pct":3.5,
            "lot":500, **overrides
        })

    def test_exact_selected_strike_and_denominator(self):
        d=self.detail(self.call())
        self.assertEqual(d["strike"],"106")
        self.assertEqual(d["underlying_ltp"],"100")
        self.assertEqual(d["premium_pct"],"3.50")
        self.assertEqual(d["distance_pct"],"6.00")
        self.assertEqual(Decimal(d["lot_credit"]),Decimal("1750"))

    def test_no_substitution_or_mismatched_quotes(self):
        for call in (None,self.call(symbol="OTHER"),self.call(expiry="JAN-27"),
                     self.call(strike=104),self.call(call_ltp=4),
                     self.call(underlying_ltp=0),self.call(premium_pct=4)):
            self.assertIsNone(self.detail(call))

    def test_exact_five_percent_boundary(self):
        self.assertEqual(self.detail(self.call(strike=105))["distance_pct"],"5.00")

    def test_lot_optional(self):
        self.assertIsNone(self.detail(self.call(),lot=None)["lot_credit"])
        self.assertEqual(self.detail(self.call(),lot=None)["strike"],"106")

if __name__=="__main__":
    unittest.main()
