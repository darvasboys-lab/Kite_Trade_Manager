"""Small local-only Streamlit app: first trade remains manual in Kite."""
import os
from datetime import date, datetime, timedelta, time as clocktime
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from zoneinfo import ZoneInfo
import streamlit as st
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from store import ROOT, read, write, settings, log
from broker import kite, snapshot
from logic import validate, make_plan, ladder, final_buy_price, projected_profit, profit_advisory, BUY_LIMIT_BUFFER_PCT, price_limit
from manager import ready,stage_live,stop_and_cancel,fingerprint

load_dotenv(ROOT/'.env')
IST=ZoneInfo('Asia/Kolkata')
st.set_page_config(page_title='Kite Trade Manager V4',page_icon='📈',layout='centered')
st.title('Kite Trade Manager')
st.caption('V4.1 · Last BUY trigger 0.5% below CE · BUY limits +2% · 50% profit advisory · Free Kite Personal API · Windows PC')
st.warning('A short call can remain partially uncovered. GTTs are NOT guaranteed fills; delivery/expiry and losses must be managed in Kite. This is an unverified pilot, not a live-ready release.',icon='⚠️')
cfg=settings()
state=read('runtime.json',{}) or {}


def market_window():
    t=datetime.now(IST)
    return t.weekday()<5 and clocktime(9,20) <= t.time() <= clocktime(15,0)


def money(v):
    return '₹'+format(float(v),',.2f') if v is not None else '—'


monitor, rules, connect, demo=st.tabs(['Dashboard','Rules','Kite Login','Offline Demo'])
with monitor:
    @st.fragment(run_every='10s')
    def status():
        s=read('runtime.json',{}) or {}
        columns=st.columns(3)
        columns[0].metric('Status',s.get('status','NOT STARTED'))
        columns[1].metric('Stock shares',s.get('shares',s.get('initial_qty','—')))
        columns[2].metric('Combined P&L*',money(s.get('combined_pnl')))
        st.caption('* Broker-reported indicative P&L. Free Personal API does not supply live quotes, and P&L may be stale. Do not use it for an automated trigger.')
        st.caption('Last broker check: '+str(s.get('last_seen') or '—'))
        if s.get('projected_max') is not None:
            st.caption(f'Projected gross full-lot profit if every buy fills at trigger: {money(s["projected_max"])}; 50% alert: {money(s["profit_threshold"])}.')
        if (s.get('status')=='ACTIVE' and s.get('consider_booking')
            and s.get('last_seen') and
            0 <= (datetime.now(IST)-datetime.fromisoformat(s['last_seen'])).total_seconds() <= 90):
            st.warning('🔔 CONSIDER BOOKING PROFIT — combined indicative share + short-call P&L is above 50% of the projected profit. Check live prices and charges in Kite. Close manually only if you choose. NO exit order sent.')
        elif s.get('pnl_advisory_status'):
            st.caption(s['pnl_advisory_status'])
        st.info(s.get('reason') or 'No live GTTs staged')
        if s.get('owned'):
            st.dataframe([{'GTT':r['id'],'Role':r['role'],'Side':r['side'],
                           'Trigger':r['trigger'],'Shares':r['quantity'],
                           'Limit':r['limit'],'State':r.get('gtt_status','unknown'),
                           'Filled':r.get('filled',0)} for r in s['owned']],hide_index=True,use_container_width=True)
        if s.get('submitting'):
            st.error('A submission may have been accepted but not recorded. Check ALL Kite GTTs manually before restarting.')
        f=ROOT/'events.log'
        if f.exists():
            with st.expander('Recent events'):
                st.code('\n'.join(f.read_text(encoding='utf-8').splitlines()[-12:]),language='text')
    status()
    st.subheader('Safety controls')
    if st.button('■ STOP & CANCEL TRACKED GTTS',type='primary',use_container_width=True):
        try:
            s,issues=stop_and_cancel()
            if issues:
                st.error('Cancellation NOT fully verified: '+'; '.join(issues))
            else:
                st.success('All known active GTTs cancelled or already inactive. Also check exchange orders in Kite.')
            st.rerun()
        except Exception as exc:
            st.error(f'Stop was not confirmed: {type(exc).__name__}: {exc}. Check Kite immediately.')
    if state.get('status') in ('STOPPED','REVIEW_REQUIRED','STOP_PENDING'):
        with st.expander('Reset tracking for a NEW trade (manual verification required)'):
            st.write('First inspect the GTT and order tabs in Kite. Confirm every old GTT is inactive and all generated orders are settled/cancelled. A reset does NOT cancel broker orders.')
            typed=st.text_input('Type RESET AFTER MANUAL RECONCILIATION',key='reset_text')
            if st.button('Reset old tracking',disabled=typed!='RESET AFTER MANUAL RECONCILIATION'):
                try:
                    k=kite()
                    outstanding=[]
                    for r in (read('runtime.json',{}) or {}).get('owned',[]):
                        g=k.get_gtt(r['id'])
                        if g.get('status')=='active':
                            outstanding.append(r['id'])
                    if outstanding:
                        st.error('Cannot reset: GTTs still active: '+str(outstanding))
                    else:
                        write('runtime.json',{})
                        log('Tracking reset only after explicit user attestation; verify all Kite orders','RESET')
                        st.success('Tracking reset. No broker order was created.')
                        st.rerun()
                except Exception as exc:
                    st.error('Reset blocked, cannot verify Kite: '+str(exc))
    st.caption('If PC is off, or access token expires, GTTs remain live at Zerodha. STOP is not complete until Kite confirms cancellation. SELL GTTs may require DDPI/PoA or daily CDSL TPIN authorization.')

with rules:
    st.subheader('Configure the price ladder')
    st.caption('Example: 100 shares at ₹1,300, 1 short ₹1,400 CE; final 100 shares bought at ₹1,393 (0.5% below strike).')
    active=state.get('status') in ('ACTIVE','STAGING','STOP_PENDING')
    with st.form('setup'):
        a,b=st.columns(2)
        stock=a.text_input('NSE share symbol',cfg['stock_symbol']).strip().upper()
        opt=b.text_input('Exact sold CE symbol',cfg['option_symbol']).strip().upper()
        exp=date.fromisoformat(cfg['expiry']) if cfg.get('expiry') else datetime.now(IST).date()+timedelta(days=30)
        expiry=st.date_input('Short-call expiry',exp)
        a,b=st.columns(2)
        base=a.number_input('First manual batch average price ₹',min_value=.01,value=float(cfg['base_price']),step=1.)
        strike=b.number_input('CE strike ₹',min_value=.01,value=float(cfg['strike']),step=10.)
        a,b=st.columns(2)
        initial=a.number_input('Initially held shares',min_value=1,value=int(cfg['initial_shares']))
        maxshares=b.number_input('Max shares = short CE quantity',min_value=1,value=int(cfg['max_shares']))
        st.caption('Remaining shares: equal-sized batches. Their prices are evenly spaced between first average and the LAST buy at least 0.5% BELOW the CE strike (rounded down to valid tick).')
        premium=st.number_input('Total short-call premium RECEIVED ₹ (all option units)',min_value=0.0,value=float(cfg.get('premium_received_total',0.0)),step=100.0,format='%.2f',help='Enter the full gross credit received when you manually SOLD the call in Kite, not the per-share option price. Verify against Kite trades.')
        a,b=st.columns(2)
        down=a.number_input('Downside price ₹',min_value=.01,value=float(cfg['down_price']),step=1.)
        downsell=b.number_input('Sell shares at downside',min_value=1,value=int(cfg['down_sell_qty']))
        a,b=st.columns(2)
        tick=a.number_input('NSE price tick size ₹',min_value=.01,value=float(cfg['tick_size']),step=.01,format='%.2f')
        buffer=b.number_input('Downside SELL limit buffer %',min_value=.1,max_value=2.0,value=float(cfg['limit_buffer_pct']),step=.1)
        st.caption(f'Every BUY GTT uses a fixed +{BUY_LIMIT_BUFFER_PCT:.0f}% limit above its trigger (rounded UP to a valid tick). This is NOT a guarantee of execution.')
        mode=st.radio('Execution mode',['PAPER','LIVE'],index=['PAPER','LIVE'].index(cfg['mode']),horizontal=True)
        save=st.form_submit_button('Save rules',disabled=active,use_container_width=True)
    if save:
        new={'stock_symbol':stock,'option_symbol':opt,'expiry':expiry.isoformat(),
             'base_price':float(base),'strike':float(strike),
             'initial_shares':int(initial),'max_shares':int(maxshares),
             'premium_received_total':float(premium),
             'step':float(cfg.get('step',20.)),'batch_qty':int(cfg.get('batch_qty',100)),'ladder_mode':'AUTO_HALF_PERCENT','down_price':float(down),
             'down_sell_qty':int(downsell),'tick_size':float(tick),
             'limit_buffer_pct':float(buffer),'mode':mode}
        try:
            validate(new)
            if (read('runtime.json',{}) or {}).get('status') in ('ACTIVE','STAGING','STOP_PENDING'):
                raise ValueError('Existing GTTs must be stopped before changing rules')
            write('settings.json',new)
            st.session_state.pop('preview',None)
            log('Rules saved (does not create or modify broker orders)')
            st.success('Rules saved. No broker action taken.')
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    try:
        validate(cfg)
        st.write('**Automatically calculated buy ladder**')
        st.caption(f'{cfg["max_shares"]-cfg["initial_shares"]} remaining shares · first average ₹{cfg["base_price"]:,.2f} · strike ₹{cfg["strike"]:,.2f} · final buy ₹{final_buy_price(cfg):,.2f}')
        for i,p,q in ladder(cfg):
            limit=price_limit(p,'BUY',cfg['tick_size'],BUY_LIMIT_BUFFER_PCT)
            st.write(f'₹{p:,.2f} → BUY {q} {cfg["stock_symbol"]} (LIMIT up to ₹{limit:,.2f})')
        st.write(f'₹{cfg["down_price"]:,.2f} → SELL {cfg["down_sell_qty"]} shares; cancel remaining buys and require review')
        gross=projected_profit(cfg)
        st.metric('Projected full-lot gross profit at strike (conditional)',money(gross))
        st.metric('Consider Booking Profit when combined P&L >',money(gross*0.5))
        st.warning('The final BUY TRIGGER is 0.5% below strike, but its +2% LIMIT may be ABOVE strike. A fill at the limit can reduce covered-call profit. Best available exchange price can be lower; no fill is guaranteed.')
        st.caption('Projected full-lot profit assumes trigger-price fills, NOT +2% limit-price fills, so it can overstate actual profit. Indicative free-API P&L may be stale; the 50% booking alert is advisory only, and ALL exits remain manual.')
    except Exception as exc:
        st.warning('Complete rules: '+str(exc))
    st.divider()
    st.subheader('Stage live GTTs')
    if cfg['mode']!='LIVE':
        st.info('PAPER mode selected. Try Offline Demo; no broker changes are permitted.')
    elif state.get('status') not in (None,'IDLE','STOPPED') or state.get('owned'):
        st.warning('A previous session exists. Stop, inspect Kite GTTs and reset tracking before staging again.')
    else:
        st.caption('Use a currently displayed Kite price during an open NSE session. This is NOT taken from stale P&L.')
        ltp=st.number_input('Current share LTP manually read from Kite ₹',min_value=.01,value=1300.,step=1.)
        if st.button('1. Validate Kite positions and preview GTTs',use_container_width=True):
            try:
                if not market_window():
                    raise ValueError('Live GTT staging permitted only Mon–Fri, 09:20–15:00 IST. Exchange holidays must also be checked manually.')
                k=kite(); plan,snap=ready(k,cfg,ltp)
                st.session_state['preview']={'cfg_hash':fingerprint(cfg),'price':ltp,
                    'at':datetime.now(IST).isoformat(), 'account':snap['user_id']}
                st.success(f'Exact stock + short option verified for Kite account {snap["user_id"]}.')
            except Exception as exc:
                st.session_state.pop('preview',None)
                st.error(f'Preview blocked: {type(exc).__name__}: {exc}')
        p=st.session_state.get('preview')
        if p and p.get('cfg_hash')==fingerprint(cfg):
            try:
                age=(datetime.now(IST)-datetime.fromisoformat(p['at'])).total_seconds()
                plan=make_plan(cfg,p['price'])
                st.table([{'Role':x.role,'Side':x.side,'Trigger':x.trigger,
                           'Qty':x.quantity,'Limit':x.limit} for x in plan])
                st.caption('Reference Kite LTP: '+money(p['price'])+f'; checked {int(age)} seconds ago. Preview expires in 60 seconds.')
                last_buy=next((x for x in reversed(plan) if x.side=='BUY'),None)
                if last_buy and last_buy.limit > float(cfg['strike']):
                    st.warning(f'IMPORTANT: final BUY trigger ₹{last_buy.trigger:,.2f} has a limit of ₹{last_buy.limit:,.2f}, ABOVE the ₹{float(cfg["strike"]):,.2f} CE strike. This is a maximum allowable price, not the expected fill.')
                tp=st.checkbox('I have authorised equity sells (DDPI/PoA or CDSL TPIN), verified option lot and expiry, and accept that BUY orders can execute up to 2% ABOVE their triggers (possibly above CE strike), may partially fill or fail')
                confirm=st.text_input('Type CREATE LIVE GTTS to authorise real broker triggers',key='live_confirm')
                unlocked=os.getenv('ENABLE_LIVE_GTTS')=='YES_I_ACCEPT_RISK'
                if not unlocked:
                    st.error('Live gate remains locked in .env: ENABLE_LIVE_GTTS=NO')
                if st.button('2. CREATE LIVE BROKER GTTS',type='primary',
                    disabled=not (tp and confirm=='CREATE LIVE GTTS' and unlocked and age<=60 and market_window()),
                    use_container_width=True):
                    try:
                        if (datetime.now(IST)-datetime.fromisoformat(p['at'])).total_seconds()>60:
                            raise ValueError('LTP preview expired. Recheck Kite price.')
                        k=kite()
                        if k.profile().get('user_id')!=p['account']:
                            raise ValueError('Kite account changed')
                        result=stage_live(k,cfg,p['price'])
                        st.session_state.pop('preview',None)
                        st.success(f'{len(result["owned"])} live GTTs created. Keep PC worker running and check Kite GTT tab.')
                        st.rerun()
                    except Exception as exc:
                        st.error('LIVE staging failed or is uncertain: '+str(exc)+'; check Kite GTT tab before any retry.')
            except Exception as exc:
                st.error('Preview invalid: '+str(exc))
    st.caption('The program never directly places the first stock purchase or short option. No algorithmic option entry or exit is supported.')

with connect:
    st.subheader('Kite session · manual daily login')
    key=os.getenv('KITE_API_KEY','').strip(); secret=os.getenv('KITE_API_SECRET','').strip()
    if not key or not secret:
        st.info('Copy .env.example to .env and enter your personal API key and API secret locally on your PC.')
    else:
        kc=KiteConnect(api_key=key)
        st.link_button('1. Open official Kite login',kc.login_url(),use_container_width=True)
        token_text=st.text_input('2. Paste redirected URL or request_token',type='password')
        if st.button('3. Connect',disabled=not token_text):
            try:
                t=parse_qs(urlparse(token_text).query).get('request_token',[''])[0] or token_text.strip()
                sess=kc.generate_session(t,api_secret=secret)
                kc.set_access_token(sess['access_token'])
                p=kc.profile()
                write('session.json',{'access_token':sess['access_token'],'login_date':date.today().isoformat(),'user_id':p.get('user_id')})
                st.success('Connected to your Kite account '+str(p.get('user_id')))
            except Exception as exc:
                st.error(f'Login failed: {type(exc).__name__}: {exc}')
    if st.button('Read positions and holdings (no orders)'):
        try:
            k=kite();net=k.positions()['net'];hs=k.holdings()
            st.write('Short calls:')
            st.dataframe([{'Symbol':p.get('tradingsymbol'),'Short qty':-int(p['quantity']),'P&L (indicative)':p.get('pnl')}
                          for p in net if p.get('exchange')=='NFO' and str(p.get('tradingsymbol','')).endswith('CE') and int(p.get('quantity',0))<0],hide_index=True)
            st.write('Equity holdings / today positions:')
            st.dataframe([{'Symbol':p.get('tradingsymbol'),'Qty':p.get('quantity'),'Source':'holding'} for p in hs if p.get('exchange')=='NSE']+
                         [{'Symbol':p.get('tradingsymbol'),'Qty':p.get('quantity'),'Source':'position'} for p in net if p.get('exchange')=='NSE'],hide_index=True)
        except Exception as exc:
            st.error(f'Kite read failed: {type(exc).__name__}: {exc}')
    st.caption('Access token stored only in session.json on this PC. Never share .env or session.json. Login typically needs refreshing each trading day.')

with demo:
    st.subheader('Offline price ladder demonstration')
    st.caption('Only simulation. No Kite login or API calls, and nothing is ordered.')
    demo_key=(cfg['base_price'],cfg['strike'],cfg['initial_shares'],cfg['max_shares'],
              cfg['tick_size'],cfg.get('ladder_mode','AUTO_HALF_PERCENT'),cfg.get('premium_received_total',0),cfg['down_price'],cfg['down_sell_qty'])
    if 'demo' not in st.session_state or st.session_state.get('demo_key')!=demo_key:
        st.session_state.demo={'shares':int(cfg['initial_shares']),'done':[], 'exited':False,'history':[]}
        st.session_state.demo_key=demo_key
    d=st.session_state.demo
    px=st.number_input('Simulated share price ₹',value=float(cfg['base_price']),min_value=.01,step=1.)
    st.metric('Shares in simulation',d['shares'])
    try:
        demo_plan=ladder(cfg)
    except ValueError as exc:
        st.error('Cannot simulate this ladder: '+str(exc));st.stop()
    next_levels=[(n,p,q) for n,p,q in demo_plan if n not in d['done']]
    if st.button('Simulate price tick',disabled=d['exited']):
        if px<=float(cfg['down_price']):
            q=min(int(cfg['down_sell_qty']),d['shares']);d['shares']-=q;d['exited']=True
            d['history'].append(f'₹{px:.2f} → SELL {q}; halt and cancel other buys')
        else:
            crossed=[(n,p,q) for n,p,q in next_levels if px>=p]
            if len(crossed)>1:
                d['exited']=True;d['history'].append('Skipped multiple unfilled levels; review rather than place duplicate orders')
            elif len(crossed)==1:
                n,p,q=crossed[0];d['shares']+=q;d['done'].append(n)
                d['history'].append(f'₹{px:.2f} → BUY {q}; shares now {d["shares"]}')
            else:d['history'].append(f'₹{px:.2f} → no change')
        st.rerun()
    for message in reversed(d['history'][-8:]):
        st.write(message)
    if st.button('Reset demo'):
        del st.session_state.demo;st.rerun()
