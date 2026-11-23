import unittest
from decimal import Decimal
from dhan_visual.strategy import Position, plan
from dhan_visual.payoff import current_expiry_payoff, expiry_breakevens, theoretical_call_price, theoretical_today_payoff


class PayoffTests(unittest.TestCase):
    def setUp(self):
        self.p = Position(Decimal('1000'), 100, Decimal('1200'), 500, Decimal('10000'))
        self.v = plan(self.p, 4)

    def test_current_initial_expiry_uncovered_above_strike(self):
        self.assertEqual(current_expiry_payoff(self.p, self.v, 0, 1200), Decimal('30000'))
        self.assertEqual(current_expiry_payoff(self.p, self.v, 0, 1500), Decimal('-90000'))

    def test_planned_expiry_capped(self):
        self.assertEqual(current_expiry_payoff(self.p, self.v, 4, 1300), self.v.projected_profit_at_strike)

    def test_current_and_planned_breakeven(self):
        be = expiry_breakevens(self.p, self.v, 0)
        self.assertEqual(be.current_expiry[0], Decimal('900'))
        self.assertEqual(be.current_expiry[1], Decimal('1275'))
        self.assertEqual(be.planned_expiry[0], Decimal('1077'))

    def test_theoretical_call_at_expiry(self):
        self.assertEqual(theoretical_call_price(1250, 1200, 25, 0), 50)

    def test_today_scenario_finite(self):
        result = theoretical_today_payoff(self.p, self.v, 0, 1100, 25, 15)
        self.assertTrue(result.is_finite())

    def test_option_model_guards(self):
        with self.assertRaises(ValueError):
            theoretical_call_price(1100, 1200, 0, 12)
        with self.assertRaises(ValueError):
            current_expiry_payoff(self.p, self.v, 0, -1)


if __name__ == '__main__':
    unittest.main()
