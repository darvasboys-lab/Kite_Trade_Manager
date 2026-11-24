"""Regression checks: one-click universal content-fit and minimal symbol table."""
import unittest

from dhan_visual.table_layout import (
    autofit_column_config, autofit_enabled, click_autofit, dataframe_key,
)


class AutoFitLayoutTests(unittest.TestCase):
    def test_one_click_enables_content_fit_for_every_configured_column(self):
        default = {
            "Symbol": {"label": "Stock", "width": "small"},
            "EMA 200": {"label": "EMA 200", "width": "medium", "help": "200-day EMA"},
            "5%+ CE premium (%)": {
                "label": "CE ≥+5%", "width": 175, "format": "%.2f%%"
            },
            "Monthly R1": {"label": "R1"},  # No predefined width
        }
        state = {}
        self.assertFalse(autofit_enabled(state))
        self.assertIs(autofit_column_config(default, False), default)
        self.assertEqual(dataframe_key("cash", state), "cash_autofit_0")

        click_autofit(state)
        self.assertTrue(autofit_enabled(state))
        fitted = autofit_column_config(default, autofit_enabled(state))
        self.assertEqual(state["ccs_autofit_revision"], 1)
        self.assertEqual(dataframe_key("cash", state), "cash_autofit_1")
        self.assertEqual(dataframe_key("straddle", state), "straddle_autofit_1")
        self.assertEqual(dataframe_key("manage", state), "manage_autofit_1")
        self.assertTrue(all(spec["width"] is None for spec in fitted.values()))
        self.assertEqual(fitted["EMA 200"]["help"], "200-day EMA")
        self.assertEqual(fitted["5%+ CE premium (%)"]["format"], "%.2f%%")
        self.assertEqual(default["EMA 200"]["width"], "medium")
        self.assertIsNone(autofit_column_config(None, True))

        # Re-click re-mounts all three data grids after user manual drag resizing.
        click_autofit(state)
        self.assertEqual(state["ccs_autofit_revision"], 2)
        self.assertEqual(dataframe_key("cash", state), "cash_autofit_2")

    def test_malformed_values_are_not_needed_for_normal_operation(self):
        self.assertFalse(autofit_enabled({}))
        self.assertEqual(dataframe_key("screener", {}), "screener_autofit_0")


if __name__ == "__main__":
    unittest.main()
