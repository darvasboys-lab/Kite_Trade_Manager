import unittest
from decimal import Decimal
from types import SimpleNamespace
from dhan_visual.smart_suggestions import bullish_checks

class SmartSuggestionsTests(unittest.TestCase):
    def test_exact_strict_comparisons(self):
        levels=SimpleNamespace(ema200=Decimal("100.001"),ema50=Decimal("100"),ema20=Decimal("99.999"),monthly_r1=None)
        self.assertEqual(bullish_checks(Decimal("100"),levels),{"ema200":False,"ema50":False,"ema20":True,"monthly_r1":None})
    def test_invalid_prices_never_pass(self):
        levels=SimpleNamespace(ema200=1,ema50=2,ema20=3,monthly_r1=4)
        for price in (None,0,-1,float("nan"),float("inf"),True):
            self.assertTrue(all(v is None for v in bullish_checks(price,levels).values()))
    def test_missing_and_invalid_levels(self):
        levels=SimpleNamespace(ema200=0,ema50=float("nan"),ema20=True)
        self.assertTrue(all(v is None for v in bullish_checks(100,levels).values()))
    def test_all_four_above(self):
        levels=SimpleNamespace(ema200=10,ema50=20,ema20=30,monthly_r1=40)
        self.assertTrue(all(v is True for v in bullish_checks(50,levels).values()))

if __name__=="__main__":
    unittest.main()
