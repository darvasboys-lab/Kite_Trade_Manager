"""Single-expiry, hypothetical read-only strategy payoff. No order or margin APIs."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Leg:
    kind: str
    side: str
    expiry: str
    strike: float
    units: int
    price: float

    def __post_init__(self):
        if (self.kind not in ("CE", "PE", "Equity", "Futures")
                or self.side not in ("Buy", "Sell")
                or type(self.units) is not int or self.units <= 0
                or not math.isfinite(self.price) or self.price < 0
                or not math.isfinite(self.strike) or self.strike < 0
                or (self.kind in ("CE", "PE") and self.strike <= 0)
                or (self.kind != "Equity" and not self.expiry)):
            raise ValueError("Invalid hypothetical strategy leg.")

    def payoff(self, spot):
        if self.kind == "CE":
            value = max(spot - self.strike, 0)
        elif self.kind == "PE":
            value = max(self.strike - spot, 0)
        else:
            value = spot
        return (1 if self.side == "Buy" else -1) * self.units * (value - self.price)


def expiry_legs(legs, expiry):
    return [leg for leg in legs if leg.kind == "Equity" or leg.expiry == expiry]


def payoff(legs, spot):
    return sum(leg.payoff(spot) for leg in legs)


def payoff_summary(legs):
    """Exact extrema on S >= 0, including the unbounded right-hand tail."""
    if not legs:
        return dict(max_profit=0., max_loss=0., breakevens=[], flat_zero=False)
    points = sorted({0., *(leg.strike for leg in legs if leg.kind in ("CE", "PE"))})
    values = [payoff(legs, p) for p in points]
    slope = sum((1 if leg.side == "Buy" else -1)*leg.units
                for leg in legs if leg.kind in ("CE", "Equity", "Futures"))
    roots = []
    flat_zero = False
    for i, (x, y) in enumerate(zip(points, values)):
        if abs(y) < 1e-8:
            roots.append(x)
        if i:
            a, b = points[i-1], values[i-1]
            if abs(y) < 1e-8 and abs(b) < 1e-8:
                flat_zero = True
            elif b*y < 0:
                roots.append(a - b*(x-a)/(y-b))
    if slope:
        tail = points[-1] - values[-1]/slope
        if tail > points[-1]:
            roots.append(tail)
    elif abs(values[-1]) < 1e-8:
        flat_zero = True
    return dict(max_profit=float("inf") if slope > 0 else max(0., max(values)),
                max_loss=float("inf") if slope < 0 else max(0., -min(values)),
                breakevens=sorted(set(roots)), flat_zero=flat_zero)
