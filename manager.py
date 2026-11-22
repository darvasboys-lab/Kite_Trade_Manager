"""Single-strategy GTT manager.

Only explicit user arm calls place_gtt. Background monitor is read-only except
cancelling owned GTTs on a safety event. Never creates new GTTs automatically.
"""
import hashlib, json, os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from filelock import FileLock
from broker import snapshot, other_orders, other_gtts, kite
from logic import make_plan, validate, triggered_order_id, profit_advisory
from store import ROOT, read, write, log, settings

IST=ZoneInfo('Asia/Kolkata')
GUARD=FileLock(str(ROOT/'control.lock'),timeout=10)


def fingerprint(cfg):
    return hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()


def ready(k,cfg,reference_ltp):
    plan=make_plan(cfg,reference_ltp)
    snap=snapshot(k,cfg,verify_instrument=True)
    if snap['shares'] != int(cfg['initial_shares']):
        raise RuntimeError(f'Your Kite shares are {snap["shares"]}, not the configured initial {cfg["initial_shares"]}')
    if other_orders(k,cfg):
        raise RuntimeError('There is an open exchange stock order. Reconcile it in Kite.')
    if other_gtts(k,cfg,set()):
        raise RuntimeError('Other active GTTs on this stock exist. Cancel/review them in Kite first.')
    # Sell GTT can fail without depository TPIN authorisation. Requires user attestation in UI.
    return plan, snap


def stage_live(k,cfg,reference_ltp):
    """Called after user's typed LIVE confirmation, while holding control lock."""
    with GUARD:
        cur=read('runtime.json',{}) or {}
        if cur.get('status') not in (None,'STOPPED','IDLE') or cur.get('owned'):
            raise RuntimeError('Old tracking state exists. Resolve/cancel outstanding GTTs first.')
        if cfg != settings() or cfg.get('mode') != 'LIVE':
            raise RuntimeError('Rules changed or mode is not LIVE')
        if os.getenv('ENABLE_LIVE_GTTS','NO') != 'YES_I_ACCEPT_RISK':
            raise RuntimeError('Live GTT creation disabled in .env')
        plan, snap=ready(k,cfg,reference_ltp)
        st={'status':'STAGING','symbol':cfg['stock_symbol'], 'option_symbol':cfg['option_symbol'],
            'user_id':snap['user_id'],'cfg_hash':fingerprint(cfg),'initial_qty':snap['shares'],
            'owned':[],'submitting':None,'created_at':datetime.now(IST).isoformat(),
            'last_seen':None,'reason':'Creating broker GTTs; do not close app'}
        write('runtime.json',st)
        try:
            for lv in plan:
                # Durable intent before irreversible request. On uncertain network timeout
                # we intentionally cannot retry: it might create a duplicate GTT.
                st['submitting']={'role':lv.role,'side':lv.side,'trigger':lv.trigger,'quantity':lv.quantity}
                write('runtime.json',st)
                response=k.place_gtt(trigger_type='single',tradingsymbol=cfg['stock_symbol'],
                    exchange='NSE',trigger_values=[lv.trigger],last_price=float(reference_ltp),
                    orders=[{'transaction_type':lv.side,'quantity':lv.quantity,
                             'order_type':'LIMIT','product':'CNC','price':lv.limit}])
                gid=int(response['trigger_id'])
                st['owned'].append({'id':gid,'role':lv.role,'side':lv.side,'trigger':lv.trigger,
                                    'quantity':lv.quantity,'limit':lv.limit,'gtt_status':'active',
                                    'filled':0,'order_id':None})
                st['submitting']=None
                write('runtime.json',st)
                log(f'Created {lv.role} {lv.side} {lv.quantity} @ {lv.trigger:.2f}, GTT {gid}', 'GTT')
            st['status']='ACTIVE'; st['reason']='Broker GTTs active; PC monitor must remain on'
            write('runtime.json',st)
            log(f'{len(plan)} broker GTTs staged for {cfg["stock_symbol"]}', 'ARM')
            return st
        except Exception as exc:
            st['status']='STOP_PENDING'
            st['reason']='Staging failed/uncertain; MANUALLY REVIEW ALL GTTs in Kite, including possibly unrecorded GTT. '+str(exc)[:160]
            write('runtime.json',st)
            # Best-effort cancellation of all KNOWN created GTTs. Never assume the
            # timed-out place_gtt did not create an additional, unrecorded GTT.
            issues=_cancel_owned(k,st)
            st['status']='REVIEW_REQUIRED'
            st['reason'] += (' | '+ '; '.join(issues)) if issues else ''
            write('runtime.json',st)
            log('GTT staging failed. Known triggers cancellation attempted; inspect Kite GTT orderbook for unrecorded GTT!', 'ERROR')
            # Do not retry place_gtt, and do not clear uncertain submission.
            raise


def _cancel_owned(k,st,only_buy=False):
    """Cancel known active GTTs, verifying each one with broker. No direct exchange order cancel."""
    issues=[]
    for record in st.get('owned',[]):
        if only_buy and record['side']!='BUY':
            continue
        gid=record['id']
        try:
            g=k.get_gtt(gid)
            status=g.get('status')
            if status=='active':
                k.delete_gtt(gid)
                status=k.get_gtt(gid).get('status')
            record['gtt_status']=status
            if status=='active':
                issues.append(f'GTT {gid} still active')
            if status=='triggered':
                issues.append(f'GTT {gid} already triggered; check resulting exchange order')
        except Exception as exc:
            issues.append(f'GTT {gid} not confirmed cancelled ({type(exc).__name__})')
        write('runtime.json',st)
    if st.get('submitting'):
        issues.append('A place_gtt request had uncertain outcome; inspect all Kite GTTs')
    return issues


def stop_and_cancel(k=None):
    """Stop local management and remove known active GTTs; fail loudly if broker inaccessible."""
    with GUARD:
        st=read('runtime.json',{}) or {}
        if not st.get('owned') and not st.get('submitting'):
            st['status']='STOPPED'; st['reason']='No tracked broker GTTs';write('runtime.json',st)
            return st, []
        st['status']='STOP_PENDING';st['reason']='Cancellation in progress; verify in Kite'
        write('runtime.json',st)
        try:
            k=k or kite()
            issues=_cancel_owned(k,st)
        except Exception as exc:
            issues=[f'Could not connect to Kite ({type(exc).__name__}). GTTs MAY STILL BE ACTIVE.']
        st['status']='REVIEW_REQUIRED' if issues else 'STOPPED'
        st['reason']='; '.join(issues) if issues else 'Known active GTTs cancelled; confirm exchange orderbook in Kite'
        write('runtime.json',st)
        log(st['reason'],'STOP')
        return st,issues


def _abort_locked(k,st,reason):
    st['status']='STOP_PENDING';st['reason']=reason
    write('runtime.json',st)
    issues=_cancel_owned(k,st)
    st['status']='REVIEW_REQUIRED'
    st['reason']=reason + ((' | Cancellation issues: '+'; '.join(issues)) if issues else ' | Remaining known active GTTs cancelled')
    write('runtime.json',st)
    log(st['reason'],'WARNING')
    return st


def monitor(k=None,now=None):
    """One worker cycle. Never creates a GTT or places a direct trade."""
    now=now or datetime.now(IST)
    with GUARD:
        st=read('runtime.json',{}) or {}
        if st.get('status')!='ACTIVE':
            return st
        cfg=settings()
        if not k:
            try:
                k=kite()
            except Exception as exc:
                # Broker-side GTTs remain active when the PC cannot authenticate.
                # Never claim local pause means the broker triggers were removed.
                st['status']='REVIEW_REQUIRED'
                st['reason']=f'Kite login unavailable ({type(exc).__name__}). BROKER GTTS MAY STILL BE ACTIVE; re-login and cancel manually.'
                write('runtime.json',st)
                log(st['reason'],'AUTH')
                return st
        try:
            if fingerprint(cfg)!=st.get('cfg_hash'):
                return _abort_locked(k,st,'Rules changed after activation')
            if now.date()>=datetime.fromisoformat(cfg['expiry']).date()-timedelta(days=3):
                return _abort_locked(k,st,'Expiry buffer reached. Review stock/option settlement')
            snap=snapshot(k,cfg)
            if snap['user_id'] != st.get('user_id'):
                return _abort_locked(k,st,'Connected Kite account changed')
            st.update({'last_seen':now.isoformat(),'shares':snap['shares'],
                       'stock_pnl':snap['stock_pnl'],'call_pnl':snap['call_pnl'],
                       'combined_pnl':snap['combined_pnl']})
            # Advisory only: NO order, GTT deletion, position close, or option action.
            advisory=profit_advisory(cfg,snap['combined_pnl']) if snap.get('pnl_available',True) else None
            was_flagged=bool(st.get('consider_booking'))
            st['consider_booking']=bool(advisory and advisory['consider_booking'])
            st['profit_threshold']=advisory['threshold'] if advisory else None
            st['projected_max']=advisory['projected_max'] if advisory else None
            if st['consider_booking'] and not was_flagged:
                log('CONSIDER BOOKING PROFIT — indicative combined P&L crossed 50% projected ceiling; manual review in Kite only','ADVISORY')
            if not advisory:
                st['pnl_advisory_status']='Unavailable: incomplete broker P&L; no profit alert'
            else:
                st['pnl_advisory_status']='Indicative only; free API P&L may be stale. Confirm in Kite.'
            outstanding=other_orders(k,cfg)
            all_filled=0
            any_down=False
            any_pending=False
            for r in st['owned']:
                g=k.get_gtt(r['id'])
                # Guard against user's manual modification of an owned GTT.
                cond=g.get('condition') or {}
                orders=g.get('orders') or []
                if (cond.get('tradingsymbol')!=cfg['stock_symbol'] or cond.get('exchange')!='NSE'
                    or len(cond.get('trigger_values',[]))!=1
                    or abs(float(cond['trigger_values'][0])-r['trigger'])>0.0001
                    or len(orders)!=1 or orders[0].get('transaction_type')!=r['side']
                    or int(orders[0].get('quantity') or 0)!=r['quantity']
                    or orders[0].get('product')!='CNC' or orders[0].get('order_type')!='LIMIT'
                    or orders[0].get('exchange')!='NSE' or orders[0].get('tradingsymbol')!=cfg['stock_symbol']
                    or abs(float(orders[0].get('price') or 0)-r['limit'])>0.0001):
                    return _abort_locked(k,st,'Managed GTT externally modified; review in Kite')
                r['gtt_status']=g.get('status')
                if r['gtt_status']=='triggered':
                    if r['side']=='SELL':
                        any_down=True
                    try:
                        oid=triggered_order_id(g)
                    except ValueError as exc:
                        return _abort_locked(k,st,str(exc))
                    r['order_id']=oid
                    h=k.order_history(oid)[-1]
                    if str(h.get('order_id'))!=oid:
                        return _abort_locked(k,st,'Order history does not match triggered GTT')
                    q=int(h.get('filled_quantity') or 0)
                    if not 0<=q<=r['quantity']:
                        return _abort_locked(k,st,'Invalid fill quantity from Kite')
                    r['filled']=q
                    if h.get('status')!='COMPLETE' or q!=r['quantity']:
                        if h.get('status') in ('REJECTED','CANCELLED'):
                            return _abort_locked(k,st,f'GTT {r["id"]} order {oid} {h.get("status")} with {q} filled')
                        any_pending=True
                elif r['gtt_status']!='active':
                    return _abort_locked(k,st,f'GTT {r["id"]} changed to {r["gtt_status"]}; review')
                all_filled+=r['filled']*(1 if r['side']=='BUY' else -1)
                write('runtime.json',st)
            owned_order_ids={r.get('order_id') for r in st['owned'] if r.get('order_id')}
            unexpected=[o for o in outstanding if str(o.get('order_id')) not in owned_order_ids]
            if unexpected:
                return _abort_locked(k,st,'Untracked open stock order exists; review Kite orders')
            expected=int(st['initial_qty'])+all_filled
            if snap['shares'] != expected:
                # No automatic new GTTs or assumptions on delayed/unsettled position updates.
                return _abort_locked(k,st,f'Kite share count {snap["shares"]}, tracked fills expect {expected}; reconcile first')
            if any_down:
                return _abort_locked(k,st,'Downside SELL GTT triggered; remaining buys must be cancelled; review sale')
            if any_pending:
                # Do not claim GTT triggers are equivalent to confirmed fills.
                st['reason']='A GTT exchange order is pending; monitor Kite for fills'
            else:
                st['reason']='GTTs monitored; no fresh orders created by worker'
            write('runtime.json',st)
            return st
        except Exception as exc:
            return _abort_locked(k,st,f'Monitor could not verify broker: {type(exc).__name__}: {str(exc)[:140]}')
