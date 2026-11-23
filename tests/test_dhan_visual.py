import unittest
from decimal import Decimal
from dhan_visual.strategy import Position, plan, exposure_after_fills, current_closeout, expiry_payoff


class DhanVisualTests(unittest.TestCase):
    def setUp(self):
        self.p = Position(Decimal('1000'), 100, Decimal('1200'), 500, Decimal('10000'))

    def test_three_auto_batches(self):
        x = plan(self.p, 3)
        self.assertEqual([s.shares for s in x.stages], [133, 133, 134])
        self.assertEqual([s.trigger for s in x.stages], [Decimal('1064.65'), Decimal('1129.35'), Decimal('1194')])
        self.assertEqual(sum(s.shares for s in x.stages), 400)

    def test_four_auto_batches(self):
        x = plan(self.p, 4)
        self.assertEqual([s.trigger for s in x.stages], [Decimal('1048.50'), Decimal('1097'), Decimal('1145.50'), Decimal('1194')])
        self.assertEqual([s.shares for s in x.stages], [100] * 4)
        self.assertEqual(x.projected_profit_at_strike, Decimal('61500'))
        self.assertEqual(x.profit_target, Decimal('30750'))

    def test_last_trigger_0_5_pct_below(self):
        x = plan(Position(Decimal('1200'), 100, Decimal('1400'), 500, Decimal('20000')), 5)
        self.assertEqual(x.final_trigger, Decimal('1393'))

    def test_partial_coverage(self):
        x = plan(self.p, 3)
        self.assertEqual(exposure_after_fills(self.p, x, 1)[0], 233)
        self.assertEqual(exposure_after_fills(self.p, x, 3)[0], 500)

    def test_closeout_no_auto_sell(self):
        x = plan(self.p, 4)
        self.assertEqual(current_closeout(self.p, x, 0, 1100, 18), Decimal('11000'))

    def test_expiry_cap(self):
        x = plan(self.p, 4)
        self.assertEqual(expiry_payoff(self.p, x, 1200), Decimal('61500'))
        self.assertEqual(expiry_payoff(self.p, x, 1300), Decimal('61500'))
        self.assertEqual(expiry_payoff(self.p, x, 1100), Decimal('11500'))

    def test_reject_nan(self):
        with self.assertRaises(ValueError):
            plan(Position(Decimal('NaN'), 100, Decimal('1200'), 500, Decimal('10000')), 4)

    def test_reject_invalid_batches(self):
        with self.assertRaises(ValueError):
            plan(self.p, 0)
        with self.assertRaises(ValueError):
            plan(self.p, 21)

    def test_reject_tight_levels(self):
        with self.assertRaises(ValueError):
            plan(Position(Decimal('1193.95'),100,Decimal('1200'),500,Decimal('10000')),4)

    def test_reject_overcovered(self):
        with self.assertRaises(ValueError):
            plan(Position(Decimal('1000'),600,Decimal('1200'),500,Decimal('10000')),4)

    def test_no_option_quote_not_current_profit(self):
        with self.assertRaises(ValueError):
            current_closeout(self.p, plan(self.p,4),0,1100,-2)


if __name__ == '__main__':
    unittest.main()
