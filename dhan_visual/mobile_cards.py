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


def is_phone_user_agent(user_agent: str) -> bool:
    """Phone browsers including Android Chrome, iPhone and mobile WebViews."""
    agent = (user_agent or "").lower()
    return any(marker in agent for marker in ("android", "iphone", "ipod", "windows phone", "mobile"))


def phone_layout() -> bool:
    """No extra dependency, network request or device fingerprint persistence."""
    import streamlit as st
    try:
        return is_phone_user_agent(st.context.headers.get("User-Agent", ""))
    except (AttributeError, RuntimeError):
        return False
