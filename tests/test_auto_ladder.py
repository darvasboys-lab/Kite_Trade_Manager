"""Automatic equal-sized ladders; offline only and no broker calls."""
import unittest
from logic import ladder,validate,make_plan,final_buy_price,projected_profit,profit_advisory

BASE={'stock_symbol':'RELIANCE','option_symbol':'RELIANCE26DEC1400CE',
      'expiry':'2099-12-31','strike':1400.,'base_price':1300.,
      'ladder_mode':'AUTO_HALF_PERCENT','initial_shares':100,'max_shares':500,'premium_received_total':10000.,
      'down_price':1280.,'down_sell_qty':100,'tick_size':0.05,
      'limit_buffer_pct':0.5,'mode':'PAPER'}
class AutoLadderTests(unittest.TestCase):
    def test_original_example(self):
        self.assertEqual(ladder(BASE),[(1,1323.25,100),(2,1346.50,100),(3,1369.75,100),(4,1393.00,100)])
    def test_equal_shares_when_not_divisible(self):
        c={**BASE,'initial_shares':140}
        levels=ladder(c)
        self.assertEqual([qty for _,_,qty in levels],[120,120,120])
        self.assertEqual([price for _,price,_ in levels],[1331,1362,1393])
        self.assertEqual(sum(q for _,_,q in levels)+140,500)
    def test_distribute_uneven_remainder(self):
        c={**BASE,'initial_shares':151}
        levels=ladder(c)
        self.assertEqual([q for _,_,q in levels],[117,116,116])
        self.assertEqual(sum(q for _,_,q in levels)+151,500)
    def test_rounding_to_tick_keeps_order(self):
        c={**BASE,'strike':1351.3,'base_price':1322.15,'initial_shares':125,
           'down_price':1300}
        levels=ladder(c)
        self.assertTrue(all(abs(round(price/.05)-price/.05)<1e-8 for _,price,_ in levels))
        self.assertTrue(all(levels[i][1]<levels[i+1][1] for i in range(len(levels)-1)))
        self.assertEqual(levels[-1][1],final_buy_price(c))
        self.assertLessEqual(levels[-1][1],c['strike']*.995)
    def test_too_small_gap_rejects_duplicates(self):
        c={**BASE,'strike':1300.1,'tick_size':0.05}
        with self.assertRaisesRegex(ValueError,'gap too small'):ladder(c)
    def test_fully_covered_rejected_for_new_plan(self):
        with self.assertRaisesRegex(ValueError,'fully covered'):validate({**BASE,'initial_shares':500})
    def test_plan_quantity_and_trigger(self):
        levels=make_plan(BASE,1300)
        self.assertEqual([(l.trigger,l.quantity) for l in levels if l.side=='BUY'],
                         [(1323.25,100),(1346.50,100),(1369.75,100),(1393.00,100)])
    def test_final_limit_no_more_than_half_percent_below_strike(self):
        levels=make_plan(BASE,1300)
        self.assertEqual(levels[-2].trigger,1393.0)
        self.assertEqual(levels[-2].limit,1393.0)
        self.assertTrue(all(v.limit<=1393.0 for v in levels if v.side=='BUY'))
    def test_profit_ceiling_and_strict_50_percent_advisory(self):
        self.assertEqual(projected_profit(BASE),36750.0)
        below=profit_advisory(BASE,18375.0)
        self.assertFalse(below['consider_booking'])
        self.assertTrue(profit_advisory(BASE,18375.01)['consider_booking'])
        self.assertFalse(profit_advisory(BASE,-1000)['consider_booking'])
        self.assertIsNone(profit_advisory(BASE,None))
    def test_missing_premium_blocks_live_plan(self):
        with self.assertRaisesRegex(ValueError,'premium'):
            validate({**BASE,'premium_received_total':0})
    def test_no_legacy_mode_allowed(self):
        with self.assertRaisesRegex(ValueError,'Previous ladder mode'):
            validate({**BASE,'ladder_mode':'AUTO'})
    def test_different_lot_batches_and_last_distance(self):
        c={**BASE,'initial_shares':77,'max_shares':327}
        levels=ladder(c)
        self.assertEqual(sum(q for _,_,q in levels),250)
        self.assertEqual(levels[-1][1],1393.0)
        self.assertLessEqual(levels[-1][1],.995*c['strike'])
    def test_tick_rounds_final_away_from_strike(self):
        c={**BASE,'strike':1401.23}
        self.assertEqual(final_buy_price(c),1394.20)
    def test_other_stock_sizes(self):
        c={**BASE,'base_price':750,'strike':810,'initial_shares':80,
           'max_shares':280,'down_price':730,'down_sell_qty':80}
        levels=ladder(c)
        self.assertEqual([q for _,_,q in levels],[67,67,66])
        self.assertEqual([p for _,p,_ in levels],[768.65,787.3,805.95])

if __name__=='__main__':unittest.main()
