"""Presentation-only light-theme regression checks for the local Streamlit UI."""
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dhan_visual" / "app.py"
CONFIG = ROOT / ".streamlit" / "config.toml"


class PayoffStudioLightThemeTests(unittest.TestCase):
    def test_project_streamlit_theme_is_light(self):
        cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        theme = cfg["theme"]
        self.assertEqual(theme["base"], "light")
        self.assertEqual(theme["backgroundColor"].upper(), "#FDF9FA")
        self.assertEqual(theme["secondaryBackgroundColor"].upper(), "#FFFFFF")
        self.assertEqual(theme["textColor"].upper(), "#17253B")
        self.assertEqual(theme["primaryColor"].upper(), "#BD425A")

    def test_chart_and_visuals_are_coherently_light(self):
        source = APP.read_text(encoding="utf-8")
        # Light remains the default; dark is a reversible opt-in theme.
        self.assertIn('st.session_state["ccs_dark_mode"] = False', source)
        self.assertIn('template="plotly_dark" if st.session_state.get("ccs_dark_mode") else "plotly_white"', source)
        self.assertIn('paper_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff"', source)
        self.assertIn('plot_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff"', source)
        self.assertIn('key="ccs_theme_button"', source)
        self.assertIn('st.session_state["ccs_dark_mode"] = not st.session_state["ccs_dark_mode"]', source)
        self.assertIn("--page: #fdf9fa", source)
        self.assertIn("--rose: #bf4259", source)
        self.assertIn("background:#fff0f3", source)
        self.assertIn("color:#a9314a !important", source)
        self.assertIn("border-top: 3px solid #db6a7c", source)
        self.assertIn("--surface: #ffffff", source)
        self.assertIn("rgba(33,155,113,.11)", source)
        self.assertIn("rgba(216,73,87,.10)", source)
        self.assertIn("stTabs [data-baseweb", source)
        self.assertIn("@media (max-width: 700px)", source)

    def test_no_strategy_changes_and_all_quote_sources_retained(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("current_expiry_payoff", source)
        self.assertIn("current_closeout", source)
        self.assertIn("render_screener()", source)
        ui = (ROOT / "dhan_visual" / "screener_ui.py").read_text(encoding="utf-8")
        self.assertIn('"Quote source", [AUTO_SOURCE, "FYERS", "Dhan", "Yahoo Finance"]', ui)
        self.assertIn('key="fno_compact_contract_value"', ui)
        self.assertIn("render_atm_panel", ui)
        self.assertIn("render_straddle_panel(available, expiry,", ui)
        self.assertIn("priority_levels=technicals, priority_quotes=prices", ui)
        from dhan_visual.straddle_scan import straddle_from_atm
        self.assertTrue(callable(straddle_from_atm))


if __name__ == "__main__":
    unittest.main()
