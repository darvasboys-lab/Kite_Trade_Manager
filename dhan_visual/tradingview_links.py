"""Safe NSE equity chart links for the Covered Call Strategy Builder.

User-clickable TradingView chart links only; no network calls, browser JS,
TradingView authentication, Pine Script uploads, or broker order actions.
The raw NSE symbol stays in the underlying stock data and CSV export.
"""
from __future__ import annotations

from urllib.parse import quote

from .screener import SYMBOL_PATTERN

# LinkColumn display_text extracts exactly the original unencoded NSE ticker
# from a harmless URL fragment. Its query uses a correctly URL-encoded NSE:
# exchange-prefixed ticker, including ampersands such as "M&M".
TRADINGVIEW_SYMBOL_DISPLAY = r"#stock=([A-Z0-9&._-]+)$"


def tradingview_chart_url(nse_symbol: str) -> str:
    """Construct a fixed-domain TradingView NSE cash-stock chart link."""
    if not isinstance(nse_symbol, str) or not SYMBOL_PATTERN.fullmatch(nse_symbol):
        raise ValueError("Invalid NSE stock symbol for TradingView")
    chart_symbol = quote(f"NSE:{nse_symbol}", safe="")
    # The fragment is display-only metadata for Streamlit's LinkColumn regex.
    # It does not alter the chart query or send the symbol to any extra site.
    return (
        "https://www.tradingview.com/chart/"
        f"?symbol={chart_symbol}#stock={nse_symbol}"
    )


def clickable_stock_frame(frame, *, source: str, target: str):
    """Copy a pandas dataframe, retaining its raw symbol and adding link.

    The source symbol is intentionally retained for dataframe numeric sorting,
    tests and any downstream logic; the UI hides this internal column and shows
    one clickable link labeled 'Stock' instead.
    """
    if source not in frame.columns:
        return frame.copy()
    result = frame.copy()
    result.insert(
        list(result.columns).index(source),
        target,
        [tradingview_chart_url(sym) for sym in result[source]],
    )
    return result
