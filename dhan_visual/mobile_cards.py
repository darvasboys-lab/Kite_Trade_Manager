"""Pure display helpers for existing CCS result snapshots."""
import math
import pandas as pd


def card_pages(count: int) -> int:
    return max(1, (count + 7) // 8)


def card_value(value, column: str) -> str:
    if pd.isna(value):
        return "—"
    if isinstance(value, str):
        return value
    try:
        n = float(value)
        if not math.isfinite(n):
            return "—"
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if "(%)" in column:
        return f"{n:.2f}%"
    if "(₹)" in column:
        return f"₹{n:,.2f}"
    if column == "Lot size":
        return f"{n:,.0f}"
    return str(value)
