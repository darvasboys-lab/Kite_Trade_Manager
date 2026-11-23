"""Pure, side-effect-free covered-call accumulation preview calculations.

Inputs are illustrative until reconciled with broker-confirmed holdings and fills.
No broker SDK, sockets, environment reads, or order submission live here.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def money(value):
    try:
        value = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError("Invalid numeric input") from exc
    if not value.is_finite():
        raise ValueError("NaN/infinity are forbidden")
    return value


@dataclass(frozen=True)
class Position:
    manual_avg: Decimal
    manual_shares: int
    strike: Decimal
    option_shares: int
    premium_received: Decimal
    stock_tick: Decimal = Decimal("0.05")


@dataclass(frozen=True)
class Stage:
    index: int
    trigger: Decimal
    shares: int


@dataclass(frozen=True)
class Preview:
    stages: tuple[Stage, ...]
    final_trigger: Decimal
    price_interval: Decimal
    remaining: int
    initial_cost: Decimal
    projected_all_in_cost: Decimal
    projected_profit_at_strike: Decimal
    profit_target: Decimal


def plan(position: Position, batches: int, estimated_full_costs=0, profit_fraction=Decimal("0.50")) -> Preview:
    """Create N AUTO batches AFTER the initial MANUAL batch.

    The last trigger is floored to valid tick no higher than strike * 0.995.
    Quantity and projected maximum are conditional on every planned buy filling.
    """
    if type(batches) is not int or not 1 <= batches <= 20:
        raise ValueError("Choose between 1 and 20 automatic batches")
    if any(type(v) is not int for v in (position.manual_shares, position.option_shares)):
        raise ValueError("Share counts must be whole integers")
    if position.manual_shares <= 0 or position.option_shares <= position.manual_shares:
        raise ValueError("Need positive manual shares and a larger short-call quantity")
    remaining = position.option_shares - position.manual_shares
    if batches > remaining:
        raise ValueError("More batches than remaining shares")
    anchor, strike, tick, premium, fees, fraction = (
        money(position.manual_avg), money(position.strike), money(position.stock_tick),
        money(position.premium_received), money(estimated_full_costs), money(profit_fraction)
    )
    if min(anchor, strike, tick, premium) <= 0 or fees < 0 or not 0 < fraction < 1:
        raise ValueError("Positive prices, premium, tick and valid target fraction required")
    final_trigger = (strike * Decimal("0.995") / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    if final_trigger <= anchor:
        raise ValueError("Manual average is not below the 0.5%-below-strike cap")
    interval = (final_trigger - anchor) / batches
    per = remaining // batches
    stages = []
    previous = anchor
    for i in range(1, batches + 1):
        target = final_trigger if i == batches else (
            (anchor + interval * i) / tick
        ).to_integral_value(rounding=ROUND_HALF_UP) * tick
        if not previous < target <= final_trigger:
            raise ValueError("Price range too narrow for distinct tick-aligned buying stages")
        qty = per + (remaining % batches if i == batches else 0)
        stages.append(Stage(i, target, qty))
        previous = target
    all_cost = position.manual_shares * anchor + sum(s.shares * s.trigger for s in stages)
    potential = position.option_shares * strike - all_cost + premium - fees
    if potential <= 0:
        raise ValueError("Projected capped payoff is non-positive")
    return Preview(tuple(stages), final_trigger, interval, remaining,
                   position.manual_shares * anchor, all_cost, potential, potential * fraction)


def exposure_after_fills(position: Position, preview: Preview, filled_batches: int):
    if type(filled_batches) is not int or not 0 <= filled_batches <= len(preview.stages):
        raise ValueError("Invalid number of simulated completed batches")
    filled = preview.stages[:filled_batches]
    shares = position.manual_shares + sum(s.shares for s in filled)
    cost = preview.initial_cost + sum(s.shares * s.trigger for s in filled)
    return shares, cost


def current_closeout(position: Position, preview: Preview, filled_batches: int,
                     stock_bid, option_ask, estimated_close_costs=0):
    """Illustrative liquidation P&L at quoted BID and ASK.

    Call ask is price per underlying share; short call units = option_shares.
    No positive P&L is treated as a real alert until live, fresh, executable
    quotes and broker-confirmed fill amounts are supplied by an adapter.
    """
    bid, ask, fees = money(stock_bid), money(option_ask), money(estimated_close_costs)
    if bid <= 0 or ask < 0 or fees < 0:
        raise ValueError("Stock bid must be positive; option ask and costs non-negative")
    shares, invested = exposure_after_fills(position, preview, filled_batches)
    return shares * bid - invested + money(position.premium_received) - position.option_shares * ask - fees


def expiry_payoff(position: Position, preview: Preview, spot, estimated_full_costs=0):
    """Conditional full-coverage expiry payoff, not an intraday price forecast."""
    s, fees = money(spot), money(estimated_full_costs)
    if s < 0 or fees < 0:
        raise ValueError("Invalid hypothetical expiry price/cost")
    intrinsic = max(s - money(position.strike), Decimal("0"))
    return position.option_shares * s - preview.projected_all_in_cost + money(position.premium_received) - position.option_shares * intrinsic - fees
