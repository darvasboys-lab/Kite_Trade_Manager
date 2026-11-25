"""Presentation-only terminal-theme regression checks for the local Streamlit UI."""
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dhan_visual" / "app.py"
CONFIG = ROOT / ".streamlit" / "config.toml"


class PayoffStudioTerminalThemeTests(unittest.TestCase):
    def test_project_streamlit_theme_is_charcoal(self):
        cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        theme = cfg["theme"]
        self.assertEqual(theme["base"], "dark")
        self.assertEqual(theme["backgroundColor"].upper(), "#10151C")
        self.assertEqual(theme["secondaryBackgroundColor"].upper(), "#171E27")
        self.assertEqual(theme["textColor"].upper(), "#E7EDF4")
        self.assertEqual(theme["primaryColor"].upper(), "#48C9A1")

    def test_chart_and_visuals_support_both_palettes(self):
        source = APP.read_text(encoding="utf-8")
        # Charcoal is the approved default; light is reversible.
        self.assertIn('st.session_state["ccs_dark_mode"] = True', source)
        self.assertIn('template="plotly_dark" if st.session_state.get("ccs_dark_mode") else "plotly_white"', source)
        self.assertIn('paper_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff"', source)
        self.assertIn('plot_bgcolor="#171f2a" if st.session_state.get("ccs_dark_mode") else "#ffffff"', source)
        self.assertIn('key="ccs_theme_button"', source)
        self.assertIn('st.session_state["ccs_dark_mode"] = not st.session_state["ccs_dark_mode"]', source)
        theme = (ROOT / "dhan_visual" / "terminal_theme.py").read_text(encoding="utf-8")
        for expected in ("#10151c", "#171e27", "#48c9a1", "#eef2f6", "#ffffff", "#08795b",
                         "stTabs [data-baseweb", "@media(max-width:760px)", "input:checked"):
            self.assertIn(expected, theme)
        self.assertIn('apply_terminal_theme(st.session_state["ccs_dark_mode"])', source)
        self.assertIn("rgba(33,155,113,.11)", source)
        self.assertIn("rgba(216,73,87,.10)", source)

    def test_no_strategy_changes_and_all_quote_sources_retained(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("current_expiry_payoff", source)
        self.assertIn("current_closeout", source)
        self.assertIn("render_screener()", source)
        ui = (ROOT / "dhan_visual" / "screener_ui.py").read_text(encoding="utf-8")
        self.assertIn('["FYERS", "Yahoo Finance"] if active_broker()', ui)
        self.assertIn('[AUTO_SOURCE, "Dhan", "Yahoo Finance"]', ui)
        self.assertIn('render_single_broker_selector()', source)
        self.assertIn('key="fno_compact_contract_value"', ui)
        self.assertIn("render_atm_panel", ui)
        self.assertIn("render_straddle_panel(available, expiry,", ui)
        self.assertIn("priority_levels=technicals, priority_quotes=prices", ui)
        from dhan_visual.straddle_scan import straddle_from_atm
        self.assertTrue(callable(straddle_from_atm))


if __name__ == "__main__":
    unittest.main()
