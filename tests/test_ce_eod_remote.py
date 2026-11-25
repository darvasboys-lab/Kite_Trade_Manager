"""No-network tests for the official NSE EOD report downloader."""
import unittest
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from dhan_visual.ce_eod_remote import (
    IST, archive_url, report_candidates, fetch_published_nse_eod,
)
from test_ce_eod_local import save_nse_zip


class FakeResponse:
    def __init__(self, status, payload=b""):
        self.status_code = status
        self.payload = payload
        self.headers = {"content-length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start:start + chunk_size]


class AutomaticEodTests(unittest.TestCase):
    def test_1am_after_gurpurab_holiday_uses_23_nov_report(self):
        now = datetime(2026, 11, 25, 1, 27, tzinfo=IST)
        dates = report_candidates(now)
        self.assertEqual(dates[0], date(2026, 11, 23))
        self.assertNotIn(date(2026, 11, 24), dates)
        self.assertEqual(
            archive_url(dates[0]),
            "https://nsearchives.nseindia.com/content/fo/"
            "BhavCopy_NSE_FO_0_0_0_20261123_F_0000.csv.zip"
        )

    def test_after_6pm_try_current_day_then_holiday_backtrack(self):
        now = datetime(2026, 11, 25, 18, 30, tzinfo=IST)
        self.assertEqual(report_candidates(now)[:2],
                         [date(2026, 11, 25), date(2026, 11, 23)])

    def test_official_zip_parses_dec_ce_without_browser_or_broker(self):
        now = datetime(2026, 11, 25, 1, 27, tzinfo=IST)
        with TemporaryDirectory() as folder:
            data = save_nse_zip(folder, "20261123").read_bytes()
        get = Mock(return_value=FakeResponse(200, data))
        result = fetch_published_nse_eod(now=now, get=get)
        self.assertEqual(result.checked, ("2026-11-23",))
        self.assertEqual(result.snapshot.report_date, "2026-11-23")
        self.assertEqual(result.payload, data)
        self.assertEqual(result.snapshot.quotes["DEC-26"]["TCS"].premium_pct, 2)
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(get.call_args.kwargs["stream"], True)

    def test_missing_current_archive_tries_previous_published_session(self):
        now = datetime(2026, 11, 25, 18, 30, tzinfo=IST)
        with TemporaryDirectory() as folder:
            data = save_nse_zip(folder, "20261123").read_bytes()
        get = Mock(side_effect=[FakeResponse(404), FakeResponse(200, data)])
        result = fetch_published_nse_eod(now=now, get=get)
        self.assertEqual(result.checked, ("2026-11-25", "2026-11-23"))
        self.assertEqual(result.snapshot.report_date, "2026-11-23")
        self.assertEqual(get.call_count, 2)

    def test_403_and_429_stop_without_retry_or_bypass(self):
        now = datetime(2026, 11, 25, 18, 30, tzinfo=IST)
        for status in (403, 429):
            with self.subTest(status=status):
                get = Mock(return_value=FakeResponse(status))
                result = fetch_published_nse_eod(now=now, get=get)
                self.assertIsNone(result.snapshot)
                self.assertIsNone(result.payload)
                self.assertIn(str(status), result.message)
                self.assertEqual(get.call_count, 1)

    def test_future_wrong_date_report_rejected(self):
        now = datetime(2026, 11, 25, 18, 30, tzinfo=IST)
        with TemporaryDirectory() as folder:
            wrong = save_nse_zip(folder, "20261123").read_bytes()
        get = Mock(return_value=FakeResponse(200, wrong))
        result = fetch_published_nse_eod(now=now, get=get)
        self.assertIsNone(result.snapshot)
        self.assertIn("NseEodError", result.message)


if __name__ == "__main__":
    unittest.main()
