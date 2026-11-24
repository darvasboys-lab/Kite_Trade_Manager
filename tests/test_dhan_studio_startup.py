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

    def test_no_manual_instrument_upload(self):
        source = (Path(__file__).resolve().parents[1] / "dhan_visual" /
                  "screener_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("file_uploader", source)
        self.assertIn("load_fast_catalogue", source)
        self.assertIn("Yahoo Finance", source)


if __name__ == "__main__":
    unittest.main()
