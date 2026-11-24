# Covered Call Strategy Builder — hide company + one-click universal auto-fit

## Requested table refinement
- Remove the **Company** text column from the main Search Stock screener.
  Keep the symbol (e.g. TCS), quote, lot, cash contract value, EMA levels,
  monthly R1 and +5% OTM CE premium. Company-name searches still work
  via the unchanged internal F&O stock catalogue; no backend mutation.
- Filtered stock CSV also omits Company; all other data and expiry logic
  are retained.

## One click: auto-fit ALL columns
- New compact toolbar button `↔ Auto-fit all`, next to Source.
- On click, the UI enables native Streamlit content-based width for every
  configured stock and ATM-straddle table column (width=None).
- Streamlit's own dataframe grid measures visible contents and column
  headings; this is not a hard-coded small/medium width guess.
- The button increments a shared layout revision and remounts all relevant
  tables: stock screener, ATM-straddle leaderboard, unavailable-stocks
  detail and covered-call stage table, clearing earlier manual drag widths.
- Click again at any time to re-fit after an EMA stage loads or quotes change.
- Existing numeric formats, color checks, sort, 219-universe coverage and
  horizontal scroll on narrow/mobile screens are preserved. Auto-fitting
  individual columns does not guarantee fitting an entire 10+ column
  screener into the viewport simultaneously.
- No JavaScript injection or broker/network activity is used for resizing.

## Windows CMD update
Stop running Streamlit with Ctrl+C:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Offline CI mocks Streamlit and broker calls; verify exact measured widths
and the redesigned light/rose screener on the user's Windows browser.
