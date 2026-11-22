"""Entirely offline tests. No network, no real Zerodha SDK needed."""
import os,sys,tempfile,unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from filelock import FileLock

# Import broker without installing KiteConnect. Real users install SDK via requirements.txt.
import types
sdk=types.ModuleType('kiteconnect')
sdk.KiteConnect=object
sys.modules.setdefault('kiteconnect',sdk)

from logic import ladder,make_plan,price_limit,stock_quantity,triggered_order_id,validate
import manager,store

CFG={'stock_symbol':'RELIANCE','option_symbol':'RELIANCE26DEC1400CE','expiry':'2099-12-31',
     'strike':1400.,'base_price':1300.,'step':20.,'batch_qty':100,'ladder_mode':'AUTO_HALF_PERCENT','initial_shares':100,
     'max_shares':500,'premium_received_total':10000.,'down_price':1280.,'down_sell_qty':100,'tick_size':.05,
     'limit_buffer_pct':.5,'mode':'LIVE'}

class FakeKite:
    def __init__(self):
        self.stocks=100
        self.gtts={}
        self.oid={}
        self.orders_list=[]
        self.nextid=100
        self.fail_at=None
        self.places=0
        self.deletions=[]
        self.nfo_qty=-500
        self.option_pnl=1000
        self.stock_pnl=50
    def profile(self):return {'user_id':'ABCD1234'}
    def positions(self):
        return {'net':[{'exchange':'NFO','tradingsymbol':CFG['option_symbol'],'quantity':self.nfo_qty,'pnl':self.option_pnl}]}
    def holdings(self):
        return [{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':self.stocks,'t1_quantity':0,'pnl':self.stock_pnl}]
    def instruments(self,market):
        if market=='NSE':return [{'tradingsymbol':'RELIANCE','tick_size':0.05}]
        if market=='NFO':return [{'tradingsymbol':CFG['option_symbol'],'instrument_type':'CE',
                                 'name':'RELIANCE','strike':1400.,'expiry':CFG['expiry'],'lot_size':500}]
        return []
    def orders(self):return self.orders_list
    def get_gtts(self):return list(self.gtts.values())
    def place_gtt(self,**kw):
        self.places+=1
        if self.fail_at==self.places:
            raise TimeoutError('simulated timeout (outcome unknown)')
        i=self.nextid;self.nextid+=1
        o=kw['orders'][0]
        self.gtts[i]={'id':i,'status':'active',
            'condition':{'exchange':kw['exchange'],'tradingsymbol':kw['tradingsymbol'],
                         'last_price':kw['last_price'],'trigger_values':kw['trigger_values']},
            'orders':[{'exchange':'NSE','tradingsymbol':kw['tradingsymbol'],
                       **o,'result':None}]}
        return {'trigger_id':i}
    def get_gtt(self,i):return self.gtts[i]
    def delete_gtt(self,i):
        if self.gtts[i]['status']=='active':
            self.gtts[i]['status']='deleted';self.deletions.append(i)
        else:raise RuntimeError('Cannot delete triggered GTT')
    def order_history(self,order_id):return [self.oid[str(order_id)]]
    def trigger(self,i,fill=True,failed=False,partial=0):
        g=self.gtts[i];g['status']='triggered'
        oid=str(i)+'1234'
        g['orders'][0]['result']={'order_result':{'status':'failed' if failed else 'success','order_id':'' if failed else oid,'rejection_reason':'Insufficient margin' if failed else ''}}
        if not failed:
            q=g['orders'][0]['quantity']
            n=partial if partial else (q if fill else 0)
            self.oid[oid]={'order_id':oid,'status':'COMPLETE' if fill else 'OPEN','filled_quantity':n}
            if fill:
                self.stocks+=n if g['orders'][0]['transaction_type']=='BUY' else -n

class PureTests(unittest.TestCase):
    def test_ladder(self):
        self.assertEqual([p for _,p,_ in ladder(CFG)],[1323.25,1346.50,1369.75,1393.00])
        self.assertEqual(sum(q for _,p,q in ladder(CFG))+100,500)
    def test_plan_with_sell(self):
        p=make_plan(CFG,1300,date(2026,11,22))
        self.assertEqual(len(p),5)
        self.assertEqual([(x.side,x.quantity) for x in p],[('BUY',100)]*4+[('SELL',100)])
        self.assertTrue(all(x.limit>=x.trigger for x in p if x.side=='BUY'))
        self.assertTrue(all(x.limit<x.trigger for x in p if x.side=='SELL'))
    def test_ref_price_missing_or_crossed(self):
        for p in [0,1280,1323.25,1346.50,1300.]:
            if p==1300.:continue
            with self.assertRaises(ValueError):make_plan(CFG,p)
    def test_proximity_zerodha(self):
        c={**CFG,'strike':1310}
        with self.assertRaisesRegex(ValueError,'0.25%|at least'):
            make_plan(c,1300)
    def test_invalid_downsize(self):
        with self.assertRaises(ValueError):validate({**CFG,'down_sell_qty':200})
    def test_expiry_gate(self):
        with self.assertRaises(ValueError):validate({**CFG,'expiry':'2026-11-24'},date(2026,11,22))
    def test_limit_rounding(self):
        self.assertEqual(price_limit(1320,'BUY',.05,.5),1326.60)
        self.assertEqual(price_limit(1280,'SELL',.05,.5),1273.60)
    def test_stock_quantity_cnc_day(self):
        h=[{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':100,'t1_quantity':0}]
        p=[{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':-20,'overnight_quantity':0,'product':'CNC'}]
        self.assertEqual(stock_quantity(h,p,'RELIANCE'),80)
    def test_quantity_avoid_overnight_doublecount(self):
        h=[{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':100}]
        p=[{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':120,'overnight_quantity':100,'product':'CNC'}]
        self.assertEqual(stock_quantity(h,p,'RELIANCE'),120)
    def test_reject_collateral(self):
        with self.assertRaises(ValueError):stock_quantity([{'exchange':'NSE','tradingsymbol':'RELIANCE','quantity':100,'collateral_quantity':50}],[],'RELIANCE')
    def test_trigger_unverified(self):
        with self.assertRaises(ValueError):triggered_order_id({'orders':[{'result':None}]})
        with self.assertRaises(ValueError):triggered_order_id({'orders':[{'result':{'order_result':{'status':'failed','rejection_reason':'funds'}}}]})

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.oldroot=store.ROOT;self.oldguard=manager.GUARD
        store.ROOT=Path(self.tmp.name);manager.GUARD=FileLock(str(store.ROOT/'guard.lock'),timeout=5)
        self.env=patch.dict(os.environ,{'ENABLE_LIVE_GTTS':'YES_I_ACCEPT_RISK'})
        self.env.start()
        store.write('settings.json',CFG)
        self.k=FakeKite()
    def tearDown(self):
        self.env.stop();manager.GUARD=self.oldguard;store.ROOT=self.oldroot
        self.tmp.cleanup()
    def stage(self):return manager.stage_live(self.k,CFG,1300)
    def test_stage_creates_five_exact_gtss(self):
        s=self.stage()
        self.assertEqual(s['status'],'ACTIVE')
        self.assertEqual(len(s['owned']),5)
        self.assertEqual(self.k.places,5)
        self.assertEqual([x['trigger'] for x in s['owned']],[1323.25,1346.50,1369.75,1393.00,1280])
    def test_stage_sends_two_percent_buy_limits_and_old_sell_buffer(self):
        s=self.stage()
        self.assertEqual([x['limit'] for x in s['owned'] if x['side']=='BUY'],
                         [1349.75,1373.45,1397.15,1420.90])
        self.assertEqual([self.k.gtts[x['id']]['orders'][0]['price']
                          for x in s['owned'] if x['side']=='BUY'],
                         [1349.75,1373.45,1397.15,1420.90])
        self.assertEqual(s['owned'][-1]['limit'],1273.60)

    def test_stage_does_not_repeat_existing(self):
        self.stage()
        with self.assertRaises(RuntimeError):self.stage()
        self.assertEqual(self.k.places,5)
    def test_no_short_option_rejected(self):
        self.k.nfo_qty=0
        with self.assertRaises(RuntimeError):self.stage()
        self.assertEqual(self.k.places,0)
    def test_stage_partial_failure_has_durable_intent(self):
        self.k.fail_at=3
        with self.assertRaises(TimeoutError):self.stage()
        st=store.read('runtime.json')
        self.assertEqual(st['status'],'REVIEW_REQUIRED')
        self.assertEqual(len(st['owned']),2)
        self.assertEqual(len(self.k.deletions),2)
        self.assertEqual(st['submitting']['role'],'UP-3')
        with self.assertRaises(RuntimeError):self.stage()
    def test_advisory_only_never_exits_on_cross(self):
        self.stage()
        self.k.option_pnl=18800
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'ACTIVE')
        self.assertTrue(state['consider_booking'])
        self.assertEqual(state['profit_threshold'],18375.0)
        self.assertEqual(self.k.deletions,[])
        self.assertEqual(self.k.places,5)
        self.assertEqual(self.k.stocks,100)
        self.k.option_pnl=100
        state=manager.monitor(self.k)
        self.assertFalse(state['consider_booking'])
    def test_missing_broker_pnl_does_not_alert(self):
        self.stage()
        self.k.option_pnl=None
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'ACTIVE')
        self.assertFalse(state['consider_booking'])
        self.assertIsNone(state['profit_threshold'])
    def test_monitor_buy_fill(self):
        st=self.stage();self.k.trigger(st['owned'][0]['id'])
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'ACTIVE')
        self.assertEqual(state['shares'],200)
        self.assertEqual(state['owned'][0]['filled'],100)
        self.assertEqual(self.k.places,5) # monitor NEVER stages
    def test_monitor_sold_downside_cancels_other_buys(self):
        st=self.stage();self.k.trigger(st['owned'][-1]['id'])
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
        self.assertEqual(len(self.k.deletions),4)
        self.assertEqual(self.k.stocks,0)
    def test_monitor_order_failed(self):
        st=self.stage();self.k.trigger(st['owned'][0]['id'],failed=True)
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
        self.assertEqual(len(self.k.deletions),4)
    def test_monitor_changed_rules_cancels(self):
        self.stage();store.write('settings.json',{**CFG,'premium_received_total':12000})
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
        self.assertEqual(len(self.k.deletions),5)
    def test_monitor_manual_stock_change_cancels(self):
        self.stage();self.k.stocks=200
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
        self.assertEqual(len(self.k.deletions),5)
    def test_stop_cancels_and_verifies(self):
        self.stage()
        state,issues=manager.stop_and_cancel(self.k)
        self.assertFalse(issues)
        self.assertEqual(state['status'],'STOPPED')
        self.assertEqual(len(self.k.deletions),5)
    def test_stop_triggered_requires_review(self):
        st=self.stage();self.k.trigger(st['owned'][1]['id'])
        state,issues=manager.stop_and_cancel(self.k)
        self.assertTrue(issues)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
    def test_refuse_preexisting_gtt(self):
        self.k.place_gtt(trigger_type='single',tradingsymbol='RELIANCE',exchange='NSE',trigger_values=[1300],last_price=1290,orders=[{'transaction_type':'BUY','quantity':2,'order_type':'LIMIT','product':'CNC','price':1301}])
        with self.assertRaises(RuntimeError):self.stage()
    def test_untracked_open_stock_order(self):
        self.stage();self.k.orders_list=[{'exchange':'NSE','tradingsymbol':'RELIANCE','status':'OPEN','order_id':'out-of-band'}]
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
    def test_external_gtt_tamper(self):
        s=self.stage();self.k.gtts[s['owned'][0]['id']]['orders'][0]['price']=9999
        state=manager.monitor(self.k)
        self.assertEqual(state['status'],'REVIEW_REQUIRED')
    def test_expiry_guard(self):
        self.stage()
        from datetime import datetime
        from zoneinfo import ZoneInfo
        state=manager.monitor(self.k,now=datetime(2099,12,29,tzinfo=ZoneInfo('Asia/Kolkata')))
        self.assertEqual(state['status'],'REVIEW_REQUIRED')

if __name__=='__main__':unittest.main()
