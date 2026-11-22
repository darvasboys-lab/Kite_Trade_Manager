"""Read-only broker inspection. Live API is called only by explicit UI action or monitor cancellation."""
import os
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from store import read
from logic import stock_quantity

load_dotenv(Path(__file__).with_name('.env'))

def kite():
    api_key=os.getenv('KITE_API_KEY','').strip()
    session=read('session.json',{}) or {}
    if not api_key or not session.get('access_token'):
        raise RuntimeError('Connect to Kite first in the Login tab')
    if session.get('login_date') != date.today().isoformat():
        raise RuntimeError('Your Kite session is from another day. Login again.')
    c=KiteConnect(api_key=api_key)
    c.set_access_token(session['access_token'])
    return c

def snapshot(k,cfg, verify_instrument=False):
    profile=k.profile()
    net=k.positions()['net']; holdings=k.holdings()
    calls=[p for p in net if p.get('exchange')=='NFO' and p.get('tradingsymbol')==cfg['option_symbol']]
    if len(calls)!=1 or int(calls[0].get('quantity') or 0)!=-int(cfg['max_shares']):
        raise RuntimeError('Short CE quantity is missing or mismatched; check exact options contract')
    qs=stock_quantity(holdings,net,cfg['stock_symbol'])
    if qs>int(cfg['max_shares']):
        raise RuntimeError('Reconciled stock shares exceed configured short-call exposure')
    p_stocks=[p for p in net if p.get('exchange')=='NSE' and p.get('tradingsymbol')==cfg['stock_symbol']]
    h_stocks=[p for p in holdings if p.get('exchange')=='NSE' and p.get('tradingsymbol')==cfg['stock_symbol']]
    pnl_rows=p_stocks+h_stocks+[calls[0]]
    pnl_available=bool(p_stocks or h_stocks) and all(p.get('pnl') is not None for p in pnl_rows)
    stock_pnl=sum(float(p.get('pnl') or 0) for p in p_stocks+h_stocks)
    call_pnl=float(calls[0].get('pnl') or 0)
    if verify_instrument:
        opts=[x for x in k.instruments('NFO') if x.get('tradingsymbol')==cfg['option_symbol']]
        if len(opts)!=1:
            raise RuntimeError('CE contract not found in current NFO instrument master')
        item=opts[0]
        if (str(item.get('instrument_type'))!='CE' or str(item.get('name'))!=cfg['stock_symbol']
            or str(item.get('expiry'))!=cfg['expiry']
            or abs(float(item.get('strike') or 0)-float(cfg['strike']))>0.001):
            raise RuntimeError('CE expiry / strike / underlying does not match your rules')
        lot=int(item.get('lot_size') or 0)
        if not lot or int(cfg['max_shares'])%lot:
            raise RuntimeError(f'Short CE size must equal whole option lots. Broker lot size: {lot}')
        eq=[x for x in k.instruments('NSE') if x.get('tradingsymbol')==cfg['stock_symbol']]
        if len(eq)!=1 or abs(float(eq[0].get('tick_size') or 0)-float(cfg['tick_size']))>0.000001:
            raise RuntimeError('NSE tick size unknown/mismatched. Update tick size from Kite instruments')
    return {'user_id':profile.get('user_id'), 'shares':qs,
            'stock_pnl':round(stock_pnl,2), 'call_pnl':round(call_pnl,2),
            'combined_pnl':round(stock_pnl+call_pnl,2) if pnl_available else None,
            'pnl_available':pnl_available,
            'net':net,'holdings':holdings}

def other_orders(k,cfg):
    return [x for x in k.orders() if x.get('exchange')=='NSE'
            and x.get('tradingsymbol')==cfg['stock_symbol']
            and x.get('status') not in ('COMPLETE','REJECTED','CANCELLED')]

def other_gtts(k,cfg,owned_ids):
    out=[]
    for t in k.get_gtts():
        c=t.get('condition') or {}
        if c.get('exchange')=='NSE' and c.get('tradingsymbol')==cfg['stock_symbol'] and t.get('status')=='active' and int(t['id']) not in owned_ids:
            out.append(int(t['id']))
    return out
