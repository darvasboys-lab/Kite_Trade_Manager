"""Read-only provenance for the exact OTM CE already selected by the scanner."""
from decimal import Decimal, InvalidOperation


def call_detail(call, *, symbol, expiry, premium_pct, lot=None):
    """Never infer a strike from CMP, nor substitute a newly fetched option."""
    if call is None or call.symbol != symbol or call.expiry != expiry:
        return None
    try:
        strike, spot, ltp, premium = (Decimal(str(v)) for v in (
            call.strike, call.underlying_ltp, call.call_ltp, call.premium_pct))
        if not all(v.is_finite() and v > 0 for v in (strike, spot, ltp, premium)):
            return None
        if strike < spot * Decimal("1.05") or premium != Decimal(str(premium_pct)):
            return None
        if (ltp * 100 / spot).quantize(Decimal("0.01")) != premium:
            return None
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    try:
        qty = Decimal(str(lot))
        lot_credit = ltp * qty if qty.is_finite() and qty > 0 and qty == qty.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        lot_credit = None
    return {
        "symbol": symbol, "expiry": expiry, "strike": str(strike),
        "underlying_ltp": str(spot), "call_ltp": str(ltp),
        "premium_pct": str(premium),
        "distance_pct": str(((strike / spot - 1) * 100).quantize(Decimal("0.01"))),
        "bid": None if call.bid is None else str(call.bid),
        "ask": None if call.ask is None else str(call.ask),
        "volume": call.volume,
        "lot_credit": None if lot_credit is None else str(lot_credit),
    }
