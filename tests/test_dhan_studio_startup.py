"""Execute the full Streamlit page in a hermetic browserless test environment.

Previously CI tested calculations only, allowing UI startup failures through.
"""
import unittest
from pathlib import Path


class PayoffStudioStartupTests(unittest.TestCase):
    def test_main_dashboard_executes_without_exception(self):
        from streamlit.testing.v1 import AppTest

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35).run()
        failures = [str(e.message) for e in app.exception]
        self.assertFalse(failures, "Streamlit UI exceptions: " + "; ".join(failures))
        self.assertGreaterEqual(len(app.tabs), 2, "Search Stock and Manage Trade tabs should be present")


if __name__ == "__main__":
    unittest.main()
