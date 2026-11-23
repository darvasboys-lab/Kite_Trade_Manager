"""Pure payoff curves for a partially covered short call and planned full coverage.

All amounts are hypothetical. This module never contacts a broker or trades.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import erf, exp, log, sqrt
from .strategy import Position, Preview, exposure_after_fills, money


@dataclass(frozen=True)
class PayoffBreakEvens:
    current_expiry: tuple[Decimal, ...]
    planned_expiry: tuple[Decimal, ...]


def current_expiry_payoff(position: Position, preview: Preview, completed: int, spot, costs=0):
    """Existing shares and short call, WITHOUT assuming pending shares execute."""
    s, fees = money(spot), money(costs)
    if s < 0 or fees < 0:
        raise ValueError("Invalid price or cost")
    shares, invested = exposure_after_fills(position, preview, completed)
    intrinsic = max(s - money(position.strike), Decimal(0))
    return (shares * s - invested + money(position.premium_received)
            - position.option_shares * intrinsic - fees)


def theoretical_call_price(spot, strike, volatility_pct, days_to_expiry, risk_free_pct=6.0):
    """Black-Scholes call scenario, NOT observed market data or executable ask."""
    s, k = float(money(spot)), float(money(strike))
    sigma = float(money(volatility_pct)) / 100
    days = float(money(days_to_expiry))
    rate = float(money(risk_free_pct)) / 100
    if s <= 0 or k <= 0 or sigma <= 0 or days < 0:
        raise ValueError("Invalid theoretical option model inputs")
    if days == 0:
        return max(s - k, 0.0)
    t = days / 365.0
    d1 = (log(s / k) + (rate + sigma * sigma / 2) * t) / (sigma * sqrt(t))
    d2 = d1 - sigma * sqrt(t)
    norm = lambda z: 0.5 * (1 + erf(z / sqrt(2)))
    return max(s * norm(d1) - k * exp(-rate * t) * norm(d2), 0.0)


def theoretical_today_payoff(position: Position, preview: Preview, completed: int,
                             spot, volatility_pct, days_to_expiry, risk_free_pct=6.0, costs=0):
    """Model-only mark-to-market scenario using option model at hypothetical spot."""
    s, fees = money(spot), money(costs)
    if s <= 0 or fees < 0:
        raise ValueError("Invalid price or costs")
    shares, invested = exposure_after_fills(position, preview, completed)
    call_model = Decimal(str(theoretical_call_price(s, position.strike, volatility_pct, days_to_expiry, risk_free_pct)))
    return (shares * s - invested + money(position.premium_received)
            - position.option_shares * call_model - fees)


def expiry_breakevens(position: Position, preview: Preview, completed: int, costs=0):
    """Analytic non-negative breakevens for current and conditional planned expiry."""
    fees = money(costs)
    if fees < 0:
        raise ValueError("Invalid costs")
    strike = money(position.strike)
    q = position.option_shares
    shares, spent = exposure_after_fills(position, preview, completed)
    credit = money(position.premium_received)
    raw = []
    low = (spent - credit + fees) / shares
    if 0 <= low <= strike:
        raw.append(low)
    if shares != q:
        high = (spent - credit - q * strike + fees) / (shares - q)
        if high >= strike and high >= 0:
            raw.append(high)
    planned_low = (preview.projected_all_in_cost - credit + fees) / q
    planned = (planned_low,) if 0 <= planned_low <= strike else ()
    return PayoffBreakEvens(tuple(dict.fromkeys(raw)), planned)
