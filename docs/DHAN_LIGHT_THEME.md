# Payoff Studio — Sensibull-inspired light mode (default)

Owner preference: replace the prior almost-black dashboard with an elegant,
clean light financial-workspace theme. This is an original design inspired by
the white canvas, compact trade cards and blue selected tabs in Sensibull's
public strategy-builder screenshots. It does not copy their branding,
product images, CSS, source code, or trading controls.

## What changes
- Light grey (#F6F8FB) application canvas with white cards and soft borders.
- Dark navy text, muted secondary labels, blue tabs and control highlights.
- Green for gains and red for losses without a saturated chart background.
- Compact header, filter controls, calm metrics, readable data table.
- Payoff Plotly chart changes to white canvas and faint grids, blue for
  current exposure and green for conditional fully covered position.
- Responsive spacing for iPhone and desktop.
- Project-local .streamlit/config.toml sets the actual Streamlit theme to
  light; CSS alone would leave input, popover and grid styling inconsistent.

## Unchanged
- Yahoo Finance default, Dhan alternate, 219-stock background loader.
- Client ID and TOTP settings, rupee/lakh display toggle, ATM CE/PE panel.
- Strategy/payoff calculations, constraints, filters and no-order boundary.

## Windows update (one command per line)
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Stop Streamlit with Ctrl+C before the update. Hard-refresh your browser
with Ctrl+Shift+R if stale CSS remains after restarting.

Visual acceptance requires inspecting an actual Windows screenshot. CI checks
the light theme and existing Streamlit UI tests; it is not a pixel comparison.
