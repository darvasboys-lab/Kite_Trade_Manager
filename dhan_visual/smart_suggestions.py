"""Pure technical evidence for the phone Smart Suggestions panel.

No market calls or trading. Compare the exact unrounded cash snapshot to
existing daily EMA and monthly R1 levels. Missing evidence is not a pass.
"""
from decimal import Decimal, InvalidOperation

CRITERIA = ("ema200", "ema50", "ema20", "monthly_r1")


def positive_number(value):
    if isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def bullish_checks(cmp, levels):
    """True = strictly above; False = at/below; None = not verified."""
    price = positive_number(cmp)
    checks = {}
    for key in CRITERIA:
        level = positive_number(getattr(levels, key, None))
        checks[key] = None if price is None or level is None else price > level
    return checks
