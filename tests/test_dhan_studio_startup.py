"""Exercise the actual Streamlit dashboard without network or broker access."""
import unittest
from pathlib import Path

from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.screener import Underlying


class PayoffStudioStartupTests(unittest.TestCase):
    def test_main_dashboard_executes_without_exception(self):
        from streamlit.testing.v1 import AppTest

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35)
        app.session_state["fno_universe_data"] = (
            [
                DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26": 225, "DEC-26": 225}), 11536),
                DhanUnderlying(Underlying("SBIN", "SBIN", {"NOV-26": 750, "DEC-26": 750}), 1333),
            ],
            "Verified test fixture only",
        )
        app.run()
        failures = [str(e.message) for e in app.exception]
        self.assertFalse(failures, "Streamlit UI exceptions: " + "; ".join(failures))
        self.assertGreaterEqual(len(app.tabs), 2)
        self.assertEqual(app.radio(key="fno_price_provider").value, "Dhan")

    def test_dhan_credentials_trigger_prices_and_render_contract_notional(self):
        from decimal import Decimal
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from dhan_visual.screener import Quote

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35)
        app.session_state["fno_universe_data"] = (
            [DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26": 225}), 0),
             DhanUnderlying(Underlying("SBIN", "SBIN", {"NOV-26": 750}), 0)],
            "Mock NSE list",
        )
        app.session_state["dhan_local_cid"] = "1234567890"
        app.session_state["dhan_local_access_token"] = "FAKE_LONG_TOKEN_FOR_TEST_ONLY"
        with patch("dhan_visual.screener_ui.resolve_equity_ids",
                   return_value={"TCS":11536,"SBIN":1333}) as resolve, \
             patch("dhan_visual.screener_ui.fetch_dhan_ltp",
                   return_value=({
                       "TCS": Quote(Decimal("3000"), "mock", "Dhan LTP"),
                       "SBIN": Quote(Decimal("600"), "mock", "Dhan LTP")
                   }, [])) as ltp, \
             patch("dhan_visual.screener_ui._write_cache"):
            app.run()
        self.assertFalse(app.exception, str([e.message for e in app.exception]))
        resolve.assert_called_once()
        ltp.assert_called_once()
        self.assertGreaterEqual(len(app.dataframe), 1)
        rows = app.dataframe[0].value
        self.assertIn(675000.0, rows["Contract value (₹)"].tolist())
        self.assertIn(450000.0, rows["Contract value (₹)"].tolist())

    def test_yahoo_backup_loads_prices_after_switch(self):
        from decimal import Decimal
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from dhan_visual.screener import Quote

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35)
        app.session_state["fno_universe_data"] = (
            [DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26":225}), 0)],
            "Mock NSE list",
        )
        app.run()
        with patch("dhan_visual.screener_ui._cached_yahoo",
                   return_value=({"TCS": Quote(Decimal("3000"), "mock", "Yahoo daily close")}, [])) as yahoo:
            app.radio(key="fno_price_provider").set_value("Yahoo Finance").run()
        self.assertFalse(app.exception, str([e.message for e in app.exception]))
        yahoo.assert_called_once()
        self.assertIn(675000.0, app.dataframe[0].value["Contract value (₹)"].tolist())

    def test_no_manual_instrument_upload(self):
        source = (Path(__file__).resolve().parents[1] / "dhan_visual" /
                  "screener_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("file_uploader", source)
        self.assertIn("load_fast_catalogue", source)
        self.assertIn("Yahoo Finance", source)


if __name__ == "__main__":
    unittest.main()
