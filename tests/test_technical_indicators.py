"""Offline: daily EMA 200/50/20/10 and PREVIOUS-month standard pivot R1."""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from queue import SimpleQueue
from threading import Event
from unittest.mock import MagicMock, patch

import pandas as pd

from dhan_visual.technical_indicators import (
    calculate_levels, ticker_ohlc, download_history_batch, format_level,
    TechnicalLevels,
)
from dhan_visual.technical_worker import (
    read_cached_technicals, save_cached_technicals, load_all_technicals,
    BATCH_DELAY_SECONDS, BATCH_SIZE,
)
from dhan_visual.technical_ui import (
    attach_technical_columns, advance_technical_stage, TECHNICAL_STAGES,
    STAGE_REVEAL_INTERVAL_SECONDS,
)


def daily_history(n=260, end="2026-11-23"):
    dates = pd.date_range(end=end, periods=n, freq="B")
    return pd.DataFrame({
        "Close": [100.0] * n, "High": [110.0] * n,
        "Low": [90.0] * n, "Open": [99.0] * n,
    }, index=dates)


def fixed_levels(day="2026-11-23"):
    return TechnicalLevels(
        Decimal("97.00"), Decimal("98"), Decimal("99"),
        Decimal("100"), Decimal("105"),
        Decimal("101"), day, "2026-11"
    )


class EMAMonthlyPivotTests(unittest.TestCase):
    def test_flat_260_daily_bars_have_each_ema_at_100(self):
        frame = daily_history()
        levels = calculate_levels(frame, current_month=date(2026, 11, 24))
        self.assertEqual(
            levels.all_values()[:4],
            (Decimal("100"),) * 4,
        )
        self.assertEqual(levels.asof, "2026-11-23")
        # Last completed month: October H=110, L=90, C=100.
        expected_r1 = Decimal(2) * (Decimal(110)+Decimal(90)+Decimal(100)) / 3 - Decimal(90)
        self.assertEqual(levels.monthly_r1, expected_r1)

    def test_october_r1_uses_only_completed_october_not_november_high(self):
        frame = daily_history()
        oct_rows = (frame.index.year == 2026) & (frame.index.month == 10)
        nov_rows = (frame.index.year == 2026) & (frame.index.month == 11)
        frame.loc[oct_rows, "High"] = 120.
        frame.loc[oct_rows, "Low"] = 80.
        frame.loc[oct_rows, "Close"] = 101.
        frame.loc[nov_rows, "High"] = 999.
        frame.loc[nov_rows, "Low"] = 1.
        frame.loc[nov_rows, "Close"] = 105.
        levels = calculate_levels(frame, current_month=date(2026, 11, 24))
        expected = Decimal(2)*(Decimal(120)+Decimal(80)+Decimal(101))/3 - Decimal(80)
        self.assertAlmostEqual(float(levels.monthly_r1), float(expected), places=8)
        self.assertLess(levels.monthly_r1, Decimal(200))

    def test_insufficient_200_history_is_unknown_not_fabricated(self):
        levels = calculate_levels(
            daily_history(65), current_month=date(2026, 11, 24)
        )
        self.assertIsNone(levels.ema200)
        self.assertIsNotNone(levels.ema50)
        self.assertIsNotNone(levels.ema20)
        self.assertIsNotNone(levels.ema10)

    def test_missing_previous_month_makes_r1_unavailable(self):
        frame = daily_history(15)
        levels = calculate_levels(frame, current_month=date(2026, 11, 24))
        self.assertIsNone(levels.monthly_r1)

    def test_missing_prices_do_not_get_forward_filled(self):
        frame = daily_history(210)
        frame.loc[frame.index[-5:], "Close"] = None
        levels = calculate_levels(frame, current_month=date(2026, 11, 24))
        self.assertEqual(levels.asof, frame.index[-6].date().isoformat())
        self.assertEqual(levels.ema200, Decimal("100"))

    def test_january_uses_december_last_year(self):
        dates = pd.date_range(start="2025-09-01", end="2026-01-08", freq="B")
        frame = pd.DataFrame({"Close":100., "High":110., "Low":90.}, index=dates)
        dec = frame.index.month == 12
        frame.loc[dec,"High"] = 130.
        frame.loc[dec,"Low"] = 80.
        frame.loc[dec,"Close"] = 115.
        result = calculate_levels(frame, current_month=date(2026,1,8))
        expected = Decimal(2)*(Decimal(130)+Decimal(80)+Decimal(115))/3-Decimal(80)
        self.assertAlmostEqual(float(result.monthly_r1), float(expected), places=8)

    def test_ticker_frame_supports_both_multiindex_conventions(self):
        frame = daily_history()
        first = pd.concat({"TCS.NS":frame,"SBIN.NS":frame*2},axis=1)
        second = first.swaplevel(0,1,axis=1)
        self.assertEqual(ticker_ohlc(first,"TCS",2)["Close"].iloc[-1],100.)
        self.assertEqual(ticker_ohlc(second,"SBIN",2)["Close"].iloc[-1],200.)
        self.assertTrue(ticker_ohlc(first,"NONEXISTENT",2).empty)

    @patch("yfinance.download")
    def test_single_bounded_bulk_yahoo_call_uses_two_year_daily_data(self, download):
        frame = daily_history()
        download.return_value = pd.concat(
            {"TCS.NS":frame,"SBIN.NS":frame},axis=1
        )
        output,errors = download_history_batch(
            ["TCS","SBIN"],current_month=date(2026,11,24)
        )
        self.assertEqual(set(output),{"TCS","SBIN"})
        self.assertEqual(errors,[])
        download.assert_called_once()
        kwargs=download.call_args.kwargs
        self.assertEqual(kwargs["period"],"2y")
        self.assertEqual(kwargs["interval"],"1d")
        self.assertFalse(kwargs["auto_adjust"])
        self.assertFalse(kwargs["threads"])
        self.assertEqual(kwargs["group_by"],"ticker")

    def test_compact_cells_display_numeric_level_and_check_vs_displayed_cmp(self):
        price=Decimal("100.25")
        self.assertEqual(format_level(price,101),"✅ ₹100.25")
        self.assertEqual(format_level(price,100),"₹100.25")
        self.assertEqual(format_level(price,100.25),"₹100.25")
        self.assertEqual(format_level(price,None),"— ₹100.25")
        self.assertEqual(format_level(None,100),"—")
        frame=pd.DataFrame([{"Symbol":"TCS","Last price (₹)":101.},
                            {"Symbol":"SBIN","Last price (₹)":90.}])
        decorated=attach_technical_columns(frame,{"TCS":fixed_levels()})
        self.assertEqual(decorated["EMA 200"].tolist(),["✅ ₹97.00","—"])
        self.assertEqual(decorated["Monthly R1"].tolist(),["₹105.00","—"])


class SequentialIndicatorPresentationTests(unittest.TestCase):
    def test_columns_reveal_in_requested_order_without_extra_fetches(self):
        self.assertEqual(
            [name for name, _ in TECHNICAL_STAGES],
            ["EMA 200", "EMA 50", "EMA 20", "EMA 10", "Monthly R1"],
        )
        input_frame = pd.DataFrame([
            {"Symbol": "TCS", "Last price (₹)": 101.0},
        ])
        data = {"TCS": fixed_levels()}
        expected = [
            ["✅ ₹97.00", "—", "—", "—", "—"],
            ["✅ ₹97.00", "✅ ₹98.00", "—", "—", "—"],
            ["✅ ₹97.00", "✅ ₹98.00", "✅ ₹99.00", "—", "—"],
            ["✅ ₹97.00", "✅ ₹98.00", "✅ ₹99.00", "✅ ₹100.00", "—"],
            ["✅ ₹97.00", "✅ ₹98.00", "✅ ₹99.00", "✅ ₹100.00", "₹105.00"],
        ]
        for stage, expected_values in enumerate(expected):
            table = attach_technical_columns(input_frame.copy(), data, stage=stage)
            found = [table[name].iloc[0] for name, _ in TECHNICAL_STAGES]
            self.assertEqual(found, expected_values)

    def test_stage_changes_one_at_a_time_after_fetch_finishes(self):
        self.assertGreaterEqual(STAGE_REVEAL_INTERVAL_SECONDS, 2.0)
        snap = {"stage": 0, "status": "loading", "stage_next_at": 3.0}
        self.assertFalse(advance_technical_stage(snap, 500.0))
        self.assertEqual(snap["stage"], 0)

        snap["status"] = "revealing"
        self.assertFalse(advance_technical_stage(snap, 2.99))
        self.assertTrue(advance_technical_stage(snap, 3.0))
        self.assertEqual(snap["stage"], 1)
        # Repeated rerenders at the same timestamp must not unlock all stages.
        self.assertFalse(advance_technical_stage(snap, 3.0))
        for target in range(2, 5):
            when = 3.0 + STAGE_REVEAL_INTERVAL_SECONDS * (target - 1)
            self.assertTrue(advance_technical_stage(snap, when))
            self.assertEqual(snap["stage"], target)
        self.assertEqual(snap["status"], "complete")
        self.assertFalse(advance_technical_stage(snap, 1000.0))

    def test_below_or_equal_level_has_no_cross_and_no_green_check(self):
        frame = pd.DataFrame([
            {"Symbol": "TCS", "Last price (₹)": 97.0},
            {"Symbol": "SBIN", "Last price (₹)": 100.0},
        ])
        same = {"TCS": fixed_levels(), "SBIN": fixed_levels()}
        data = attach_technical_columns(frame, same, stage=4)
        self.assertEqual(data["EMA 200"].tolist(), ["₹97.00", "✅ ₹97.00"])
        self.assertEqual(data["Monthly R1"].tolist(), ["₹105.00", "₹105.00"])
        self.assertNotIn("✕", data.to_string())
        self.assertNotIn("❌", data.to_string())


class TechnicalWorkerTests(unittest.TestCase):
    def test_cache_is_public_only_and_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.json"
            day=date(2026,11,24)
            save_cached_technicals({"TCS":fixed_levels()},day,path)
            text=path.read_text(encoding="utf-8")
            self.assertNotIn("token",text.lower())
            self.assertNotIn("client_id",text.lower())
            self.assertNotIn("pin",text.lower())
            payload=json.loads(text)
            self.assertEqual(payload["version"],1)
            found=read_cached_technicals(path,today=day)
            self.assertEqual(found["TCS"].ema200,Decimal("97"))
            self.assertEqual(
                read_cached_technicals(path,today=date(2026,12,1)),{}
            )
            stale=datetime.now(timezone.utc)+timedelta(hours=10)
            self.assertEqual(read_cached_technicals(path,today=day,now=stale),{})

    def test_all_219_batched_slowly_without_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.json"
            symbols=[f"FO{i:03d}" for i in range(219)]
            queue=SimpleQueue()
            stop=Event()
            def data(group, *, current_month):
                return {sym:fixed_levels() for sym in group},[]
            with patch("dhan_visual.technical_worker.download_history_batch",
                       side_effect=data) as downloader:
                processed,total=load_all_technicals(
                    symbols,queue,stop,today=date(2026,11,24),
                    cache_path=path,pause=0,batch_size=6
                )
            self.assertEqual((processed,total),(219,219))
            self.assertEqual(downloader.call_count,37)
            self.assertTrue(all(len(call.args[0])<=6
                                for call in downloader.call_args_list))
            self.assertGreaterEqual(BATCH_DELAY_SECONDS,5)
            self.assertLessEqual(BATCH_SIZE,10)
            chunks=[]
            while not queue.empty():
                chunks.append(queue.get_nowait())
            self.assertEqual(chunks[-1].completed,219)
            self.assertEqual(
                len(read_cached_technicals(path,today=date(2026,11,24))),219
            )

    def test_cached_subset_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.json"
            day=date(2026,11,24)
            save_cached_technicals({"A":fixed_levels()},day,path)
            queue=SimpleQueue()
            with patch("dhan_visual.technical_worker.download_history_batch",
                       return_value=({"B":fixed_levels()},[])) as fetch:
                processed,total=load_all_technicals(
                    ["A","B"],queue,Event(),today=day,cache_path=path,pause=0
                )
            self.assertEqual((processed,total),(2,2))
            fetch.assert_called_once()
            self.assertEqual(fetch.call_args.args[0],["B"])

    def test_cancel_stops_before_download(self):
        queue=SimpleQueue()
        stop=Event()
        stop.set()
        with tempfile.TemporaryDirectory() as folder:
            with patch("dhan_visual.technical_worker.download_history_batch") as fetch:
                load_all_technicals(["TCS"],queue,stop,
                                    today=date(2026,11,24),
                                    cache_path=Path(folder)/"empty.json")
                fetch.assert_not_called()

    def test_all_yahoo_failed_no_fake_values(self):
        queue=SimpleQueue()
        with tempfile.TemporaryDirectory() as folder:
            with patch("dhan_visual.technical_worker.download_history_batch",
                       return_value=({},["Yahoo rate limit"])):
                processed,total=load_all_technicals(
                    ["A"],queue,Event(),today=date(2026,11,24),
                    cache_path=Path(folder)/"cache.json",pause=0
                )
            self.assertEqual((processed,total),(1,1))
            update=queue.get_nowait()
            self.assertEqual(update.records,{})
            self.assertEqual(update.failed,("Yahoo rate limit",))


if __name__=="__main__":
    unittest.main()
