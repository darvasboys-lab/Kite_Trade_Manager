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


def phone_user_agent(value: str) -> bool:
    """Detect phones even when the CCS server runs on another OS."""
    value = (value or "").lower()
    return any(token in value for token in ("android", "iphone", "ipod", "mobile"))


def phone_layout():
    import streamlit as st
    override = st.session_state.get("ccs_phone_layout")
    if override is not None:
        return bool(override)
    try:
        return phone_user_agent(st.context.headers.get("User-Agent", ""))
    except Exception:
        return False


def card_fields(row, fields):
    """Escaped compact card grid, no untrusted HTML or invented values."""
    from html import escape
    return '<div class="ccs-card-grid">' + "".join(
        '<div><small>' + escape(label) + '</small><strong>' +
        escape(card_value(row[col], col)) + '</strong></div>'
        for col, label in fields if col in row
    ) + '</div>'
