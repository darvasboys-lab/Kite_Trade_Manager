"""Offline schedule checks for automatically refreshed official NSE EOD reports."""
from concurrent.futures import Future
from datetime import datetime
from unittest import TestCase
from unittest.mock import Mock, patch

from dhan_visual.ce_eod_remote import IST
from dhan_visual import screener_ui


class ManualPool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.calls.append((fn, future))
        return future


class AutomaticEodScheduleTests(TestCase):
    def test_once_on_startup_then_after_close_without_manual_upload(self):
        fake_st = Mock()
        fake_st.session_state = {}
        pool = ManualPool()
        with patch.object(screener_ui, "st", fake_st), \
             patch.object(screener_ui, "_eod_pool", return_value=pool), \
             patch.object(screener_ui, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 1)
            self.assertEqual(fake_st.session_state["fno_ce5_remote_attempt_key"],
                             "2026-11-25")
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 1)

            pool.calls[0][1].set_result(None)
            clock.now.return_value = datetime(2026, 11, 25, 18, 5, tzinfo=IST)
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 2)
            self.assertEqual(fake_st.session_state["fno_ce5_remote_attempt_key"],
                             "2026-11-25-after-close")
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 2)

            pool.calls[1][1].set_result(None)
            clock.now.return_value = datetime(2026, 11, 26, 9, 15, tzinfo=IST)
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 3)
            self.assertEqual(fake_st.session_state["fno_ce5_remote_attempt_key"],
                             "2026-11-26")

    def test_ongoing_fetch_is_not_duplicated_at_market_close(self):
        fake_st = Mock()
        fake_st.session_state = {}
        pool = ManualPool()
        with patch.object(screener_ui, "st", fake_st), \
             patch.object(screener_ui, "_eod_pool", return_value=pool), \
             patch.object(screener_ui, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 25, 17, 59, tzinfo=IST)
            screener_ui._eod_start()
            clock.now.return_value = datetime(2026, 11, 25, 18, 0, tzinfo=IST)
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 1)
            # The next EOD check becomes eligible after the prior job finishes.
            pool.calls[0][1].set_result(None)
            screener_ui._eod_start()
            self.assertEqual(len(pool.calls), 2)
