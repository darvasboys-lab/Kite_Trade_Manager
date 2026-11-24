"""Offline validation of safe, clickable NSE TradingView stock symbols."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
import re
import unittest

import pandas as pd

from dhan_visual.tradingview_links import (
    TRADINGVIEW_SYMBOL_DISPLAY, clickable_stock_frame, tradingview_chart_url,
)


class TradingViewLinksTests(unittest.TestCase):
    def test_standard_nse_symbol_opens_correct_exchange_qualified_chart(self):
        for symbol in ("TCS", "SBIN", "RELIANCE", "ICICIBANK"):
            with self.subTest(symbol=symbol):
                url = tradingview_chart_url(symbol)
                parts = urlsplit(url)
                self.assertEqual(parts.scheme, "https")
                self.assertEqual(parts.netloc, "www.tradingview.com")
                self.assertEqual(parts.path, "/chart/")
                self.assertEqual(parse_qs(parts.query)["symbol"], [f"NSE:{symbol}"])
                self.assertEqual(
                    re.search(TRADINGVIEW_SYMBOL_DISPLAY, url).group(1), symbol
                )

    def test_ampersand_hyphen_and_dot_are_escaped_without_losing_label(self):
        for symbol in ("M&M", "BAJAJ-AUTO", "ABC.DEF", "J&KBANK"):
            with self.subTest(symbol=symbol):
                url = tradingview_chart_url(symbol)
                parts = urlsplit(url)
                self.assertEqual(parse_qs(parts.query)["symbol"], [f"NSE:{symbol}"])
                self.assertEqual(
                    re.search(TRADINGVIEW_SYMBOL_DISPLAY, url).group(1), symbol
                )
        self.assertIn("NSE%3AM%26M", tradingview_chart_url("M&M"))

    def test_invalid_symbols_cannot_redirect_or_inject_urls(self):
        for value in (
            "", "tcs", "NSE:TCS", "TCS?x=1", "X#fragment", "/evil",
            "TCS%26URL", "TCS\nBAD", "TCS<script>", None, 123,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tradingview_chart_url(value)

    def test_only_display_copy_gets_urls_raw_stock_and_numbers_unchanged(self):
        original = pd.DataFrame([
            {"Symbol": "TCS", "Last price (₹)": 3500.0, "EMA 200": "✅ ₹3400.00"},
            {"Symbol": "M&M", "Last price (₹)": 3200.0, "EMA 200": "₹3300.00"},
        ])
        displayed = clickable_stock_frame(
            original, source="Symbol", target="TradingView chart"
        )
        self.assertEqual(
            displayed.columns.tolist(),
            ["TradingView chart", "Symbol", "Last price (₹)", "EMA 200"],
        )
        self.assertEqual(original.columns.tolist(), ["Symbol", "Last price (₹)", "EMA 200"])
        self.assertEqual(original["Symbol"].tolist(), ["TCS", "M&M"])
        self.assertEqual(displayed["Symbol"].tolist(), ["TCS", "M&M"])
        self.assertEqual(displayed["Last price (₹)"].tolist(), [3500., 3200.])
        self.assertTrue(all("tradingview.com/chart" in u for u in displayed["TradingView chart"]))
        displayed.at[0, "Last price (₹)"] = 123.
        self.assertEqual(original.at[0, "Last price (₹)"], 3500.)

    def test_straddle_stock_column_can_be_linked_without_mutating_results(self):
        original = pd.DataFrame([
            {"Rank": 1, "Stock": "SBIN", "Straddle premium (%)": 5.5},
        ])
        displayed = clickable_stock_frame(
            original, source="Stock", target="TradingView chart"
        )
        self.assertEqual(
            displayed.columns.tolist(),
            ["Rank", "TradingView chart", "Stock", "Straddle premium (%)"]
        )
        self.assertEqual(original["Stock"].tolist(), ["SBIN"])
        self.assertEqual(
            parse_qs(urlsplit(displayed["TradingView chart"][0]).query)["symbol"],
            ["NSE:SBIN"],
        )


if __name__ == "__main__":
    unittest.main()
