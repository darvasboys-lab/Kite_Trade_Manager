"""Offline single-active-broker behavior and saved-login compatibility."""
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from dhan_visual.auto_auth import AutoAccess
from dhan_visual.fyers_readonly import FyersAccess
from test_dhan_studio_startup import app_with_catalogue, FakePool


class SingleBrokerUITests(unittest.TestCase):
    def test_switch_from_dhan_to_fyers_pauses_dhan_activity(self):
        app = app_with_catalogue()
        dhan = FakePool()
        fyers = FakePool()
        with patch("dhan_visual.screener_ui._market_pool", return_value=dhan), \
             patch("dhan_visual.fyers_ui._fyers_pool", return_value=fyers), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None), \
             patch("dhan_visual.screener_ui.check_dhan_data_plan") as dhan_api, \
             patch("dhan_visual.screener_ui.read_dhan_balance") as dhan_funds:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "Dhan")
            app.radio(key="ccs_broker_requested").set_value("FYERS").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "FYERS")
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "FYERS")
            self.assertEqual(app.radio(key="fno_home_ce_source").value, "FYERS")
            self.assertEqual(app.radio(key="fno_price_provider").value, "FYERS")
            self.assertFalse(any("dhan_connect_button" == b.key for b in app.button))
            self.assertIsNotNone(app.button(key="fyers_start_login"))
            dhan_api.assert_not_called()
            dhan_funds.assert_not_called()
            self.assertEqual(len(fyers.calls), 0)  # Not authorized yet
            app.radio(key="ccs_broker_requested").set_value("Dhan").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "Dhan")
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "EOD")
            self.assertEqual(app.radio(key="fno_price_provider").value, "Auto (Dhan → Yahoo)")

    def test_fyers_mode_reuses_vaulted_fyers_only_and_never_dhan_auto_login(self):
        app = app_with_catalogue()
        app.session_state["ccs_active_broker"] = "FYERS"
        app.session_state["ccs_broker_committed"] = "FYERS"
        app.session_state["fno_ce5_mode"] = "FYERS"
        app.session_state["fno_home_ce_source"] = "FYERS"
        app.session_state["fno_price_provider"] = "FYERS"
        fyers_access = FyersAccess(
            "ABCD1234-100", "offline-fyers-session-only",
            datetime.now(timezone.utc) + timedelta(hours=8),
        )
        pool = FakePool()
        with patch("dhan_visual.fyers_ui._fyers_pool", return_value=pool), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=fyers_access), \
             patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.screener_ui.is_configured", return_value=True), \
             patch("dhan_visual.screener_ui.load_or_generate") as auto_login, \
             patch("dhan_visual.screener_ui.check_dhan_data_plan") as profile, \
             patch("dhan_visual.screener_ui.read_dhan_balance") as funds:
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="fno_ce5_mode").value, "FYERS")
            self.assertEqual(app.radio(key="fno_price_provider").value, "FYERS")
            self.assertEqual(len(pool.calls), 2)  # FYERS cash and chain shared
            auto_login.assert_not_called()
            profile.assert_not_called()
            funds.assert_not_called()

    def test_pending_switch_drains_existing_broker_worker_before_new_login(self):
        from dhan_visual.broker_control import broker_allowed
        app = app_with_catalogue()
        future = Future()
        app.session_state["dhan_auto_auth_job"] = {
            "cid": "1234567890", "future": future,
        }
        with patch("dhan_visual.fyers_ui.read_app_from_vault", return_value=None), \
             patch("dhan_visual.fyers_ui.read_access_from_vault", return_value=None):
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            app.radio(key="ccs_broker_requested").set_value("FYERS").run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.session_state["ccs_pending_broker"], "FYERS")
            self.assertEqual(app.session_state["ccs_active_broker"], "Dhan")
            self.assertIsNone(app.session_state.get("fno_fyers_cash_job"))
            self.assertIsNone(app.session_state.get("fno_fyers_chain_job"))
            future.set_result(AutoAccess(
                "OFFLINE-ONLY-DHAN-ACCESS-TOKEN", datetime.now(timezone.utc) + timedelta(hours=8),
                "test",
            ))
            app.run()
            self.assertFalse(app.exception, [str(x.message) for x in app.exception])
            self.assertEqual(app.radio(key="ccs_broker_requested").value, "FYERS")
            self.assertIsNone(app.session_state.get("ccs_pending_broker"))


if __name__ == "__main__":
    unittest.main()
