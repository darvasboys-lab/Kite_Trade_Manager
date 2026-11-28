"""V3 read-only cached market data and existing pure payoff models."""
from datetime import datetime
from math import isfinite
from .workspace_store import read
from .stock_strategy import Leg, payoff, payoff_summary
from .market_session import is_refresh_open


def home():
    try:
        data = read()
        snapshot = data["snapshot"]
        error = None
    except (OSError, ValueError, TypeError, ArithmeticError):
        snapshot, error = None, "Saved workspace could not be read. V2 data was not changed."
    return {"snapshot": snapshot, "market_open": is_refresh_open(),
            "live_refresh": False, "error": error}


def saved_payoff(symbol, preset):
    snapshot = read()["snapshot"]
    if not snapshot:
        raise ValueError("No saved snapshot.")
    row = next((r for r in snapshot["rows"] if r["Stock"] == symbol), None)
    if row is None:
        raise ValueError("Stock not in saved snapshot.")
    def val(key, positive=False):
        n = row.get(key)
        if type(n) not in (int, float) or not isfinite(n) or n < 0 or (positive and n <= 0):
            raise ValueError("Required saved quote unavailable: " + key)
        return n
    lot = val("Lot size", True)
    if int(lot) != lot:
        raise ValueError("Invalid lot size.")
    lot = int(lot)
    spot = val("CMP (₹)", True)
    expiry = snapshot["filters"]["expiry"]
    if preset == "Covered call":
        strike = val("CE strike", True)
        legs = [Leg("Equity","Buy","",0,lot,spot),
                Leg("CE","Sell",expiry,strike,lot,val("CE premium (₹)"))]
    elif preset == "Short straddle":
        strike = val("ATM strike", True)
        legs = [Leg("CE","Sell",expiry,strike,lot,val("ATM CE premium (₹)")),
                Leg("PE","Sell",expiry,strike,lot,val("ATM PE premium (₹)"))]
    else:
        raise ValueError("Unknown preset.")
    result = payoff_summary(legs)
    result = {k: ("Unlimited" if isinstance(v, float) and not isfinite(v) else v)
              for k,v in result.items()}
    lo = max(0, min(spot, strike)*0.75)
    hi = max(spot, strike)*1.25
    points = sorted({lo, hi, spot, strike, *[lo+(hi-lo)*i/60 for i in range(61)]})
    return dict(symbol=symbol, expiry=expiry, saved_at=snapshot["saved_at"],
                strike=strike, lot=lot, spot=spot, summary=result,
                points=[[x,payoff(legs,x)] for x in points])


def stages(avg, shares, strike, lot, premium, batches):
    from decimal import Decimal
    from .strategy import Position, plan
    if not (1 <= shares <= lot <= 100000 and 1 <= batches <= 20):
        raise ValueError("Invalid shares, lot or stages.")
    position = Position(Decimal(str(avg)), shares, Decimal(str(strike)), lot,
                        Decimal(str(premium)))
    result = plan(position, batches)
    return {"stages": [{"index": s.index, "trigger": float(s.trigger), "shares": s.shares}
                       for s in result.stages], "final_trigger": float(result.final_trigger)}
