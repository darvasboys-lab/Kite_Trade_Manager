"""Strongest-first queue safety: all stocks remain eligible, no guessed signals."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from dhan_visual.load_priority import strongest_first


DAY = date(2026, 11, 25)


def levels(e200="70", e50="80", e20="90", e10="100", r1="105",
           close="110", asof=DAY):
    def dec(s):
        return Decimal(str(s)) if s is not None else None
    return SimpleNamespace(
        ema200=dec(e200), ema50=dec(e50), ema20=dec(e20), ema10=dec(e10),
        monthly_r1=dec(r1), last_daily_close=dec(close),
        asof=asof.isoformat() if asof else "",
    )


class PriorityTests(unittest.TestCase):
    def test_strictly_bullish_r1_breakout_first_and_below_ema200_last(self):
        signals = {
            "STRICT": levels(),
            "STACKED": levels(r1="95"),
            "NOT_STACKED": levels(e200="70", e50="100", e20="80", e10="90", r1="105"),
            "EMA_ONLY": levels(r1=None),
            "ABOVE_200": levels(e50="130", e20="135", e10="140", r1="150"),
            "BELOW_200": levels(close="60"),
        }
        names = ["BELOW_200", "ABOVE_200", "UNKNOWN", "EMA_ONLY", "STRICT",
                 "NOT_STACKED", "STACKED"]
        ordered = strongest_first(names, signals, today=DAY)
        self.assertEqual(ordered, [
            "STRICT", "STACKED", "NOT_STACKED", "EMA_ONLY",
            "ABOVE_200", "UNKNOWN", "BELOW_200",
        ])
        self.assertEqual(set(ordered), set(names))

    def test_uses_fresh_current_quote_not_cached_last_close(self):
        data = {"A": levels(close="110"), "B": levels(close="65")}
        quotes = {"A": SimpleNamespace(price=Decimal("65")),
                  "B": SimpleNamespace(price=Decimal("110"))}
        self.assertEqual(strongest_first(["A", "B"], data, quotes, today=DAY),
                         ["B", "A"])

    def test_stale_close_does_not_classify_stock(self):
        data = {"STALE": levels(close="110", asof=DAY - timedelta(days=10)),
                "BELOW": levels(close="65")}
        self.assertEqual(strongest_first(["STALE", "BELOW"], data, today=DAY),
                         ["STALE", "BELOW"])

    def test_invalid_or_missing_values_never_create_positive_signal(self):
        data = {"NAN": levels(e200="NaN"),
                "MISSING": levels(e200=None),
                "GOOD": levels()}
        self.assertEqual(strongest_first(["NAN", "MISSING", "GOOD"], data, today=DAY),
                         ["GOOD", "MISSING", "NAN"])

    def test_duplicate_input_deduplicated_without_dropping_other_stocks(self):
        output = strongest_first(["X", "X", "Y", "Z", "Y"], {}, today=DAY)
        self.assertEqual(output, ["X", "Y", "Z"])


if __name__ == "__main__":
    unittest.main()
