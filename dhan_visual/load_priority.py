"""Deterministic strongest-first request queue using available, verified indicators.

Priority only controls *order*, never eligibility, indicator calculations or trade
signals. Unknown stocks are kept in the queue. All stocks are eventually scanned.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping


IST = timezone(timedelta(hours=5, minutes=30))


def _positive(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _price(symbol, levels, quotes, today):
    quote = (quotes or {}).get(symbol)
    current = _positive(getattr(quote, "price", quote))
    if current is not None:
        return current
    # Only for QUEUE ORDER on a recently computed local indicator cache:
    # use last completed daily close until today's cash quote is available.
    item = (levels or {}).get(symbol)
    if item is None:
        return None
    try:
        asof = date.fromisoformat(item.asof)
    except (ValueError, TypeError, AttributeError):
        return None
    if asof > today or today - asof > timedelta(days=7):
        return None
    return _positive(getattr(item, "last_daily_close", None))


def strongest_first(symbols, levels: Mapping | None = None,
                    quotes: Mapping | None = None,
                    *, today: date | None = None) -> list[str]:
    """Full bullish stack 200<50<20<10<R1<CMP first, below 200 last.

    In between: bullish EMA stack above R1, price above all levels,
    four-EMA strength, price above 200, then unclassified, then below 200.
    A missing value never turns into a positive signal.
    """
    today = today or datetime.now(IST).date()
    levels = levels or {}

    def key(symbol):
        item = levels.get(symbol)
        price = _price(symbol, levels, quotes, today)
        if item is None or price is None:
            return (6, 0, symbol)
        e200 = _positive(getattr(item, "ema200", None))
        e50 = _positive(getattr(item, "ema50", None))
        e20 = _positive(getattr(item, "ema20", None))
        e10 = _positive(getattr(item, "ema10", None))
        r1 = _positive(getattr(item, "monthly_r1", None))
        if e200 is None:
            return (6, 0, symbol)
        if price <= e200:
            return (7, 0, symbol)
        stack = all(x is not None for x in (e50, e20, e10)) and e200 < e50 < e20 < e10
        above_all_emas = all(x is not None and price > x for x in (e200, e50, e20, e10))
        above_r1 = r1 is not None and price > r1
        strict = stack and r1 is not None and e10 < r1 < price
        if strict:
            tier = 0
        elif stack and above_r1 and above_all_emas:
            tier = 1
        elif above_all_emas and above_r1:
            tier = 2
        elif stack and above_all_emas:
            tier = 3
        elif above_all_emas:
            tier = 4
        else:
            tier = 5
        # Higher relative margin above 200 EMA first within each tier.
        margin = (price - e200) / e200
        return (tier, -margin, symbol)

    return sorted(set(symbols), key=key)
