"""Entire Streamlit app stays safe with FYERS mode selected on both tabs."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dhan_visual.fyers_readonly import FyersAccess
from test_dhan_studio_startup import app_with_catalogue, FakePool


class FyersAppUITests(unittest.TestCase):
    def test_fyers_mode_both_tabs_share_a_read_only_worker_without_duplicate_ids(self):
        app = app_with_catalogue()
        app.session_state["_fyers_access"] = FyersAccess(
            "ABCD1234-100", "offline-fyers-api-test-token",
            datetime.now(timezone.utc) + timedelta(hours=9),
        )
        app.session_state["ccs_active_broker"] = "FYERS"
        app.session_state["ccs_broker_committed"] = "FYERS"
        app.session_state["fno_ce5_mode"] = "FYERS"
        app.session_state["fno_home_ce_source"] = "FYERS"
        app.session_state["fno_price_provider"] = "FYERS"
        cash = FakePool()
        history = FakePool()
        with patch("dhan_visual.fyers_ui._fyers_pool", return_value=cash), \
             patch("dhan_visual.technical_ui._tech_pool", return_value=history), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None):
            app.run()
            self.assertFalse(app.exception,
                             [str(error.message) for error in app.exception])
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "FYERS")
            self.assertEqual(app.radio(key="fno_home_ce_source").value, "FYERS")
            self.assertEqual(app.radio(key="fno_price_provider").value, "FYERS")
            # Exactly one underlying options job, plus one shared cash job.
            functions = [args[0][0].__name__ for args in cash.calls]
            self.assertEqual(functions.count("fetch_cash_quotes"), 1)
            self.assertEqual(functions.count("_fyers_chain_worker"), 1)
            app.run()
            self.assertFalse(app.exception,
                             [str(error.message) for error in app.exception])
            self.assertEqual(len(cash.calls), 2)


if __name__ == "__main__":
    unittest.main()
