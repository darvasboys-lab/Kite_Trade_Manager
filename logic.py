"""Pure calculation and state-interpretation functions. No Kite orders here."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')

@dataclass(frozen=True)
class Level:
    role: str
    side: str
    trigger: float
    quantity: int
    limit: float

def final_buy_price(cfg):
    """Last BUY trigger no closer than 0.50% below the sold call strike.

    Floor to the valid stock tick, rather than round up toward the strike.
    """
    strike=Decimal(str(cfg['strike']))
    tick=Decimal(str(cfg['tick_size']))
    base=Decimal(str(cfg['base_price']))
    if strike<=0 or base<=0 or tick<=0 or base>=strike:
        raise ValueError('Invalid first buy / CE strike / tick')
    raw=strike*Decimal('0.995')
    price=(raw/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    if price<=base:
        raise ValueError('Price gap too small for final BUY 0.5% below strike')
    return float(price)


def ladder(cfg):
    """AUTO_HALF_PERCENT: equal-sized share tranches, evenly spaced from
    first batch average price through the final trigger 0.5% below strike.
    The last trigger is floored to a valid tick (never closer than 0.5%).
    No bought-share counts are assumed until actual Kite fills are verified.
    """
    if cfg.get('ladder_mode')!='AUTO_HALF_PERCENT':
        raise ValueError('Previous ladder mode detected: open Rules and Save rules for the 0.5% ladder')
    base=Decimal(str(cfg['base_price']))
    strike=Decimal(str(cfg['strike']))
    first=int(cfg['initial_shares'])
    total=int(cfg['max_shares'])
    tick=Decimal(str(cfg['tick_size']))
    if base<=0 or strike<=base or first<=0 or total<first or tick<=0:
        raise ValueError('Require 0 < first buy < strike and 0 < first shares <= option shares')
    remaining=total-first
    if remaining==0:
        return []
    n=(remaining+first-1)//first
    if n>50:
        raise ValueError('Too many buy levels (maximum 50); increase initial shares')
    if n>remaining:
        raise ValueError('Buy levels cannot exceed remaining shares')
    last=Decimal(str(final_buy_price(cfg)))
    if (last-base)<tick*Decimal(n):
        raise ValueError('Price gap too small for unique pre-strike rungs at this tick size')
    quantities=[remaining//n+(1 if i<remaining%n else 0) for i in range(n)]
    result=[];previous=base
    for i,qty in enumerate(quantities,1):
        raw=base+(last-base)*Decimal(i)/Decimal(n)
        trigger=last if i==n else (raw/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
        if not previous<trigger<=last:
            raise ValueError('Price gap too small for unique pre-strike rungs at this tick size')
        if i!=n and trigger>=last:
            raise ValueError('Price gap too small for unique pre-strike rungs at this tick size')
        result.append((i,float(trigger),qty))
        previous=trigger
    if sum(q for _,_,q in result)!=remaining:
        raise ValueError('Automatic ladder sizing mismatch')
    return result


def projected_profit(cfg):
    """Projected gross P&L at strike AFTER all share buys at their triggers.

    Not an unconditional maximum while the sold call is uncovered, not a
    forecast, and ignores brokerage, slippage, taxes, financing and dividends.
    """
    premium=Decimal(str(cfg.get('premium_received_total',0)))
    if premium<=0:
        raise ValueError('Enter total premium received for the manually sold call')
    strike=Decimal(str(cfg['strike']))
    first=int(cfg['initial_shares'])
    total=int(cfg['max_shares'])
    if total<=first:
        raise ValueError('No remaining shares to build a projected ladder')
    costs=Decimal(str(cfg['base_price']))*first
    for _,trigger,qty in ladder(cfg):
        costs+=Decimal(str(trigger))*qty
    estimate=premium+strike*total-costs
    if estimate<=0:
        raise ValueError('Projected full-lot profit is non-positive; review rules')
    return float(estimate.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))


def profit_advisory(cfg, combined_pnl):
    """Informational P&L alert. Never invokes any broker operation."""
    if combined_pnl is None:
        return None
    projected=Decimal(str(projected_profit(cfg)))
    observed=Decimal(str(combined_pnl))
    if not observed.is_finite():
        return None
    threshold=(projected*Decimal('0.5')).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    return {'projected_max':float(projected), 'threshold':float(threshold),
            'combined_pnl':float(observed), 'consider_booking':observed>threshold}

def price_limit(trigger, side, tick, buffer_pct):
    v=Decimal(str(trigger)); t=Decimal(str(tick)); buf=Decimal(str(buffer_pct))/100
    if side=='BUY':
        raw=v*(1+buf); rounding=ROUND_CEILING
    elif side=='SELL':
        raw=v*(1-buf); rounding=ROUND_FLOOR
    else:
        raise ValueError('Unknown order side')
    if t <= 0 or raw <= 0:
        raise ValueError('Invalid limit price')
    return float((raw/t).to_integral_value(rounding=rounding)*t)

def validate(cfg, today=None):
    today=today or datetime.now(IST).date()
    for key in ('stock_symbol','option_symbol','expiry'):
        if not cfg.get(key):
            raise ValueError(f'{key} is required')
    if not str(cfg['stock_symbol']).replace('-','').replace('&','').isalnum():
        raise ValueError('Invalid stock symbol')
    if not str(cfg['option_symbol']).endswith('CE'):
        raise ValueError('Select the exact sold CE symbol')
    expiry=date.fromisoformat(cfg['expiry'])
    if expiry <= today+timedelta(days=3):
        raise ValueError('Stop new GTT staging within 3 calendar days of option expiry')
    for k in ('base_price','strike','down_price','tick_size','limit_buffer_pct'):
        if float(cfg[k]) <= 0:
            raise ValueError(f'{k} must be positive')
    for k in ('initial_shares','max_shares','down_sell_qty'):
        if int(cfg[k]) <= 0:
            raise ValueError(f'{k} must be a positive integer')
    if not float(cfg['down_price']) < float(cfg['base_price']) < float(cfg['strike']):
        raise ValueError('Require downside < initial price < call strike')
    if int(cfg['down_sell_qty']) > int(cfg['initial_shares']):
        raise ValueError('Downside sale cannot exceed initially owned shares')
    if int(cfg['max_shares']) < int(cfg['initial_shares']):
        raise ValueError('Max shares cannot be below initial shares')
    if float(cfg['limit_buffer_pct']) > 2:
        raise ValueError('Limit buffer must be <= 2%')
    if int(cfg['max_shares'])==int(cfg['initial_shares']):
        raise ValueError('Already fully covered: no additional shares remain to purchase')
    if not ladder(cfg):
        raise ValueError('No valid buy rungs below strike')
    if float(cfg.get('premium_received_total',0)) <= 0:
        raise ValueError('Enter total call premium received (₹) to enable the 50% advisory')
    if cfg.get('mode') not in ('PAPER','LIVE'):
        raise ValueError('Mode must be PAPER or LIVE')
    return True

def make_plan(cfg, reference_ltp, today=None):
    validate(cfg,today=today)
    ltp=float(reference_ltp)
    if ltp <= 0:
        raise ValueError('Enter a positive current LTP observed in Kite')
    if not float(cfg['down_price']) < ltp < ladder(cfg)[0][1]:
        raise ValueError('Current LTP must be above downside and below first BUY level. Already crossed? Review manually.')
    tick=float(cfg['tick_size'])
    b=float(cfg['limit_buffer_pct'])
    # All BUY LIMIT prices are capped at the final 0.5%-below-strike level.
    # This prevents slippage above the desired ceiling, but increases non-fill risk.
    cap=final_buy_price(cfg)
    raw=[Level(f'UP-{i}','BUY',p,qty,min(price_limit(p,'BUY',tick,b),cap)) for i,p,qty in ladder(cfg)]
    raw.append(Level('DOWN','SELL',float(cfg['down_price']),int(cfg['down_sell_qty']),price_limit(cfg['down_price'],'SELL',tick,b)))
    for lv in raw:
        # Zerodha validation: prices > 50: trigger >= 0.25% away from observed LTP.
        gap=0.0025*ltp if ltp>50 else 0.09
        if abs(lv.trigger-ltp) < gap:
            raise ValueError(f'{lv.role}: GTT must be at least {gap:.2f} away from last observed price')
        if (lv.side=='BUY' and lv.trigger <= ltp) or (lv.side=='SELL' and lv.trigger >= ltp):
            raise ValueError(f'{lv.role}: wrong trigger side relative to current LTP')
    return raw

def stock_quantity(holdings, net, symbol):
    """Holdings are carryforward/T1, CNC net positions are today's changes.
    If broker reports overnight CNC quantity in positions, exclude that overlap.
    Fail closed for ambiguous positions, pledged/discrepant holdings and other products.
    """
    hs=[x for x in holdings if x.get('exchange')=='NSE' and x.get('tradingsymbol')==symbol]
    ps=[x for x in net if x.get('exchange')=='NSE' and x.get('tradingsymbol')==symbol]
    if len(hs)>1 or len(ps)>1 or any(x.get('product')!='CNC' for x in ps):
        raise ValueError('Ambiguous holdings/CNC equity positions')
    if any(x.get('discrepancy') or int(x.get('collateral_quantity') or 0)>0 or int(x.get('used_quantity') or 0)>0 for x in hs):
        raise ValueError('Pledged/discrepant/already-sold holding requires manual reconciliation')
    opening=sum(int(x.get('quantity') or 0)+int(x.get('t1_quantity') or 0) for x in hs)
    movement=sum(int(x.get('quantity') or 0)-int(x.get('overnight_quantity') or 0) for x in ps)
    out=opening+movement
    if out<0:
        raise ValueError('Negative reconciled share quantity')
    return out

def triggered_order_id(gtt):
    results=[o.get('result') for o in gtt.get('orders',[]) if o.get('result')]
    if len(results)!=1:
        raise ValueError('Triggered GTT does not contain exactly one executed leg result')
    inner=results[0].get('order_result') or {}
    if inner.get('status') == 'failed':
        raise ValueError('GTT order rejected: '+str(inner.get('rejection_reason') or 'unknown reason'))
    order_id=inner.get('order_id')
    if not order_id:
        raise ValueError('GTT triggered without order ID; inspect Kite')
    return str(order_id)
