"""Deterministic, offline tests for automatic 219-stock quote preloading."""
import unittest
from concurrent.futures import Future
from decimal import Decimal
from queue import SimpleQueue
from unittest.mock import patch

from dhan_visual.background_quotes import (
    deduplicate_underlyings, load_dhan_all, load_yahoo_all, priority_order,
)
from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.screener import Quote, Underlying


def stocks(count=219):
    return [
        DhanUnderlying(
            Underlying(f"FO{i:04d}", f"FO{i:04d}", {"NOV-26": 750, "DEC-26": 750}),
            10000 + i,
        )
        for i in range(count)
    ]


class DhanBackgroundQuotes(unittest.TestCase):
    @patch("dhan_visual.background_quotes.fetch_dhan_ltp")
    def test_all_219_in_single_dhan_bulk_request(self, broker):
        rows = stocks()
        broker.return_value = (
            {r.entry.symbol: Quote(Decimal("600"), "mock", "Dhan LTP") for r in rows}, []
        )
        result = load_dhan_all(rows, "1234567890", "FAKE_TOKEN_TEST_ONLY_LENGTHY")
        broker.assert_called_once()
        self.assertEqual(len(broker.call_args.args[0]), 219)
        self.assertEqual(len(result.quotes), 219)
        self.assertEqual(result.requested, 219)
        self.assertFalse(result.errors)

    @patch("dhan_visual.background_quotes.fetch_dhan_ltp")
    def test_missing_stocks_retried_once_not_forever(self, broker):
        rows = stocks(219)
        part1 = {
            row.entry.symbol: Quote(Decimal("605"), "mock", "Dhan")
            for row in rows[:140]
        }
        part2 = {
            row.entry.symbol: Quote(Decimal("605"), "mock", "Dhan")
            for row in rows[140:]
        }
        broker.side_effect = [(part1, []), (part2, [])]
        result = load_dhan_all(rows, "1234567890", "FAKE_TOKEN_TEST_ONLY_LENGTHY")
        self.assertEqual(broker.call_count, 2)
        self.assertEqual(len(broker.call_args.args[0]), 79)
        self.assertEqual(len(result.quotes), 219)

    @patch("dhan_visual.background_quotes.resolve_equity_ids")
    @patch("dhan_visual.background_quotes.fetch_dhan_ltp")
    def test_missing_equity_ids_mapped_then_one_call(self, broker, ids):
        rows = stocks(3)
        rows = [DhanUnderlying(x.entry, 0) for x in rows]
        ids.return_value = {x.entry.symbol: x.equity_security_id for x in stocks(3)}
        broker.return_value = (
            {x.entry.symbol: Quote(Decimal("300"), "mock", "Dhan")
             for x in stocks(3)}, []
        )
        result = load_dhan_all(rows, "1234567890", "FAKE_TOKEN_TEST_ONLY_LENGTHY")
        self.assertEqual(len(result.ids), 3)
        self.assertTrue(all(x > 0 for x in result.ids.values()))
        broker.assert_called_once()
        self.assertEqual(len(result.quotes), 3)

    @patch("dhan_visual.background_quotes.fetch_dhan_ltp")
    def test_invalid_token_no_infinite_poll(self, broker):
        broker.return_value = ({}, ["Batch 1: HTTP 401 invalid token"])
        result = load_dhan_all(stocks(219), "1234567890", "TEST_TOKEN_FAKE_FAKE")
        broker.assert_called_once()
        self.assertFalse(result.quotes)
        self.assertIn("401", " ".join(result.errors))


class YahooBackgroundQuotes(unittest.TestCase):
    @patch("dhan_visual.background_quotes.fetch_yahoo_quotes")
    def test_all_219_processed_across_auto_25_stock_chunks(self, yahoo):
        yahoo.side_effect = (
            lambda items, batch_size: (
                {sym: Quote(Decimal("800"), "mock", "Yahoo daily close") for sym in items},
                [],
            )
        )
        queue = SimpleQueue()
        completed, total = load_yahoo_all(stocks(), queue, expiry="NOV-26", batch_size=25)
        self.assertEqual((completed, total), (219, 219))
        self.assertEqual(yahoo.call_count, 9)
        updates = []
        while not queue.empty():
            updates.append(queue.get_nowait())
        self.assertEqual(len(updates), 9)
        self.assertEqual(updates[0].completed, 25)
        self.assertEqual(updates[-1].completed, 219)
        self.assertEqual(sum(len(x.attempted) for x in updates), 219)
        self.assertEqual(sum(len(x.quotes) for x in updates), 219)

    @patch("dhan_visual.background_quotes.fetch_yahoo_quotes")
    def test_all_stocks_attempted_even_with_missing_quote(self, yahoo):
        yahoo.return_value = ({}, ["Yahoo: throttled"])
        queue = SimpleQueue()
        load_yahoo_all(stocks(50), queue, batch_size=25)
        self.assertEqual(yahoo.call_count, 2)
        self.assertEqual(queue.get_nowait().errors, ("Yahoo: throttled",))
        self.assertEqual(queue.get_nowait().completed, 50)

    def test_under_7l_priority_never_guesses_unknown_prices(self):
        rows = stocks(4)
        prices = {
            "FO0002": Quote(Decimal("900"), "old", "Yahoo"),  # 6.75 lakh
            "FO0001": Quote(Decimal("1500"), "old", "Yahoo"),  # 11.25 lakh
            "FO0003": Quote(Decimal("200"), "old", "Yahoo"),  # 1.50 lakh
        }
        order = priority_order(rows, prices, "NOV-26")
        self.assertEqual(order[:2], ["FO0003", "FO0002"])
        self.assertEqual(set(order), {r.entry.symbol for r in rows})

    def test_market_lot_price_including_219(self):
        rows = stocks()
        result = deduplicate_underlyings(rows)
        self.assertEqual(len(result), 219)
        self.assertEqual(result[0].entry.lots["NOV-26"], 750)


if __name__ == "__main__":
    unittest.main()
