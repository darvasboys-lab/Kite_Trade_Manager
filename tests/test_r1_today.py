"""Offline NSE monthly-R1 crossing tests: exact IST today and safe Yahoo pacing."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from queue import SimpleQueue
from threading import Event
from unittest.mock import patch
import unittest

import pandas as pd

from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.r1_today import (
    IST, R1TodayCross, R1ScanUpdate, R1_BATCH_SIZE, R1_BATCH_PAUSE_SEC,
    session_r1_cross, yahoo_r1_batch, scan_r1_today,
)
from dhan_visual.r1_today_panel import crossed_r1_frame
from dhan_visual.screener import Underlying
from dhan_visual.technical_indicators import TechnicalLevels


TODAY = date(2026, 11, 24)
YESTERDAY = date(2026, 11, 23)


def candles(
    old_close=90, today_rows=None, include_prior=True, prior_last=True,
    source_tz="Asia/Kolkata",
):
    if today_rows is None:
        today_rows = [
            ("09:15", 95, 99, 98),
            ("09:30", 98, 103, 99),
            ("09:45", 99, 110, 105),
        ]
    index, rows = [], []
    if include_prior:
        t = "15:15" if prior_last else "10:00"
        index.append(pd.Timestamp(f"2026-11-23 {t}", tz=IST))
        rows.append([old_close, old_close, old_close])
    for t, opened, high, close in today_rows:
        index.append(pd.Timestamp(f"2026-11-24 {t}", tz=IST))
        rows.append([opened, high, close])
    frame = pd.DataFrame(
        rows, index=pd.DatetimeIndex(index),
        columns=["Open", "High", "Close"],
    )
    return frame.tz_convert(source_tz)


def sample_event(symbol, current=102, crossed_time="09:30"):
    return R1TodayCross(
        symbol, TODAY, Decimal("100"), crossed_time,
        Decimal(str(current)), "15:15",
        current > 100, (Decimal(str(current))-100),
    )


def fixed_levels(r1="100"):
    return TechnicalLevels(
        Decimal("90"), Decimal("94"), Decimal("95"), Decimal("97"),
        Decimal(r1), Decimal("101"), "2026-11-23", "2026-11",
    )


class R1IntradayCrossTests(unittest.TestCase):
    def test_true_upward_crossing_today_and_status(self):
        item, last = session_r1_cross(candles(), Decimal("100"), TODAY)
        self.assertIsNotNone(item)
        self.assertEqual(item.first_bar_time, "09:30")
        self.assertEqual(item.day, TODAY)
        self.assertEqual(item.r1, Decimal(100))
        self.assertEqual(last, Decimal(105))
        self.assertTrue(item.currently_above)
        self.assertEqual(item.pct_vs_r1, Decimal(5))

    def test_one_hour_later_price_below_still_shows_todays_cross(self):
        rows = [
            ("09:15", 95, 98, 97),
            ("09:30", 97, 102, 99),
            ("09:45", 99, 99, 98),
        ]
        item, latest = session_r1_cross(
            candles(today_rows=rows), Decimal(100), TODAY,
        )
        self.assertEqual(item.first_bar_time, "09:30")
        self.assertFalse(item.currently_above)
        self.assertEqual(item.pct_vs_r1, Decimal(-2))
        self.assertEqual(latest, Decimal(98))

    def test_not_a_cross_if_it_was_already_above_r1_yesterday(self):
        rows = [
            ("09:15", 105, 109, 108),
            ("09:30", 108, 110, 109),
        ]
        event, _ = session_r1_cross(
            candles(old_close=107, today_rows=rows), Decimal(100), TODAY,
        )
        self.assertIsNone(event)

    def test_validated_gap_above_r1_from_prior_completed_close(self):
        bars = candles(old_close=95, today_rows=[
            ("09:15", 103, 108, 107)
        ])
        event, _ = session_r1_cross(bars, Decimal(100), TODAY)
        self.assertEqual(event.first_bar_time, "09:15")

    def test_no_prior_session_close_cannot_guess_gap_crossover(self):
        rows = [("09:15", 103, 108, 107)]
        for bars in (
            candles(today_rows=rows, include_prior=False),
            candles(today_rows=rows, prior_last=False),
        ):
            event, _ = session_r1_cross(bars, Decimal(100), TODAY)
            self.assertIsNone(event)

    def test_opens_below_and_high_crosses_during_first_candle(self):
        event, _ = session_r1_cross(
            candles(include_prior=False, today_rows=[
                ("09:15", 95, 102, 99)
            ]), Decimal(100), TODAY,
        )
        self.assertEqual(event.first_bar_time, "09:15")

    def test_yesterday_above_but_recrosses_after_today_dip(self):
        event, _ = session_r1_cross(
            candles(old_close=105, today_rows=[
                ("09:15", 105, 107, 102),
                ("09:30", 99, 104, 101),
            ]), Decimal(100), TODAY,
        )
        self.assertEqual(event.first_bar_time, "09:30")

    def test_stale_market_candles_and_timezones_fail_closed(self):
        frame = candles()
        with self.assertRaisesRegex(ValueError, "today"):
            session_r1_cross(frame, Decimal(100), date(2026, 11, 25))
        with self.assertRaisesRegex(ValueError, "timezone"):
            session_r1_cross(frame.tz_localize(None), Decimal(100), TODAY)
        as_utc = frame.tz_convert("UTC")
        event, _ = session_r1_cross(as_utc, Decimal(100), TODAY)
        self.assertEqual(event.first_bar_time, "09:30")

    def test_holiday_no_today_bars_never_false_green(self):
        frame = candles(today_rows=[])
        with self.assertRaises(ValueError):
            session_r1_cross(frame, Decimal(100), TODAY)

    def test_equal_high_is_not_crossed(self):
        event, _ = session_r1_cross(
            candles(today_rows=[("09:15", 98, 100, 100)]),
            Decimal(100), TODAY,
        )
        self.assertIsNone(event)

    def test_15_30_after_hours_candle_is_excluded(self):
        bars = candles(today_rows=[
            ("09:15", 95, 99, 99),
            ("15:30", 99, 110, 110),
        ])
        event, _ = session_r1_cross(bars, Decimal(100), TODAY)
        self.assertIsNone(event)


class YahooR1WorkerTests(unittest.TestCase):
    @patch("yfinance.download")
    def test_one_bounded_yahoo_request_for_six_stocks(self, download):
        src = candles()
        symbols = ["TCS", "SBIN"]
        download.return_value = pd.concat({
            "TCS.NS": src, "SBIN.NS": src
        }, axis=1)
        data, unknown, warning = yahoo_r1_batch(
            symbols, {"TCS": Decimal(100), "SBIN": Decimal(100)},
            today=TODAY,
        )
        self.assertEqual(set(data), set(symbols))
        self.assertEqual(unknown, 0)
        self.assertEqual(warning, ())
        download.assert_called_once()
        args = download.call_args.kwargs
        self.assertEqual(args["interval"], "15m")
        self.assertEqual(args["period"], "5d")
        self.assertFalse(args["threads"])
        self.assertFalse(args["auto_adjust"])
        self.assertFalse(args["prepost"])

    def test_219_symbols_in_37_batches_without_a_fifty_stock_cap(self):
        names = [f"FO{i:03}" for i in range(219)]
        values = {x: Decimal(100) for x in names}
        queue = SimpleQueue()
        with patch("dhan_visual.r1_today.yahoo_r1_batch",
                   side_effect=lambda group, *_args, **_kwargs: (
                       {x: sample_event(x) for x in group}, 0, ()
                   )) as fetch:
            complete, total = scan_r1_today(
                names, values, queue, Event(), today=TODAY,
                pause=0, batch_size=6,
            )
        self.assertEqual((complete, total), (219, 219))
        self.assertEqual(fetch.call_count, 37)
        self.assertTrue(all(len(call.args[0]) <= 6 for call in fetch.call_args_list))
        self.assertEqual(R1_BATCH_SIZE, 6)
        self.assertGreaterEqual(R1_BATCH_PAUSE_SEC, 5)
        rows = {}
        while not queue.empty():
            e = queue.get_nowait()
            rows.update(e.records)
        self.assertEqual(len(rows), 219)

    def test_missing_monthly_r1_skips_symbol_without_yahoo_request(self):
        queue = SimpleQueue()
        with patch("dhan_visual.r1_today.yahoo_r1_batch",
                   return_value=({"TCS": sample_event("TCS")}, 0, ())) as fetch:
            count, total = scan_r1_today(
                ["TCS", "SBIN"], {"TCS": Decimal(100)},
                queue, Event(), today=TODAY, pause=0,
            )
        self.assertEqual((count, total), (2, 2))
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], ["TCS"])
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        self.assertEqual(sum(x.unknown for x in messages), 1)

    def test_cancel_before_first_fetch(self):
        cancel = Event()
        cancel.set()
        queue = SimpleQueue()
        with patch("dhan_visual.r1_today.yahoo_r1_batch") as fetch:
            self.assertEqual(
                scan_r1_today(["TCS"], {"TCS":Decimal(100)},queue,cancel,
                              today=TODAY), (0, 1),
            )
            fetch.assert_not_called()

    def test_yahoo_outage_unknown_not_false_no_crossing(self):
        queue = SimpleQueue()
        with patch("dhan_visual.r1_today.yahoo_r1_batch",
                   side_effect=ConnectionError("rate limit")):
            self.assertEqual(
                scan_r1_today(
                    ["TCS", "SBIN"], {"TCS":Decimal(100),"SBIN":Decimal(100)},
                    queue, Event(), today=TODAY,pause=0,
                ), (2,2),
            )
        item = queue.get_nowait()
        self.assertEqual(item.records,{})
        self.assertEqual(item.unknown,2)


class R1TopPanelRowsTests(unittest.TestCase):
    def test_all_crossed_stocks_keep_complete_analysis_even_above_7l(self):
        from dhan_visual.r1_today_panel import crossed_r1_frame
        universe = [
            DhanUnderlying(Underlying("TCS", "TCS Ltd", {"NOV-26": 9000}), 11536),
            DhanUnderlying(Underlying("SBIN", "SBIN Ltd", {"NOV-26": 750}), 1333),
        ]
        crossed = {
            "TCS": sample_event("TCS",current=102),
            "SBIN": sample_event("SBIN",current=99),
        }
        levels = {"TCS": fixed_levels(), "SBIN": fixed_levels()}
        result = crossed_r1_frame(universe,"NOV-26", crossed,levels,{},
                                  compact=True)
        self.assertEqual(len(result), 2)
        self.assertEqual(set(result["Symbol"]), {"TCS","SBIN"})
        self.assertIn("EMA 200", result.columns)
        self.assertIn("EMA 50", result.columns)
        self.assertIn("EMA 20", result.columns)
        self.assertIn("EMA 10", result.columns)
        self.assertIn("Monthly R1", result.columns)
        self.assertIn("5%+ CE premium (%)", result.columns)
        self.assertIn("R1 crossed at", result.columns)
        self.assertIn("Now above R1?", result.columns)
        self.assertNotIn("Company", result.columns)
        self.assertIn("Contract value (₹L)",result.columns)
        self.assertGreater(
            result.loc[result["Symbol"]=="TCS","Contract value (₹L)"].iloc[0],7
        )
        self.assertEqual(result.iloc[0]["Symbol"],"TCS")
        self.assertEqual(result.iloc[1]["Now above R1?"],"Back below")
        self.assertEqual(
            result.loc[result["Symbol"]=="TCS","Last price (₹)"].iloc[0],102
        )


if __name__=="__main__":
    unittest.main()
