# Covered Call Strategy Builder — green tick + sequential EMA/R1 reveal

## Green above-level indicator only

- Each EMA 200/50/20/10 and monthly R1 stock-table cell contains the
  numerical technical level and a green ✅ ONLY when displayed cash CMP
  is strictly above it.
- At/below a level: show only its ₹level, no ✕/❌/red cross.
- Missing/invalid CMP: `— ₹level` (not an implied positive signal).
- No calculated indicator: `—` until a valid reading exists.
- The main stock-price source remains Yahoo by default; Dhan alternate
  is unchanged. No new options or broker orders are involved.

## Staged UI (one Yahoo history pass, five reveal stages)

- Stage 1: EMA 200 values/checks appear per stock as the existing slow
  Yahoo history worker downloads daily bars in six-stock batches.
- When the full requested history pass finishes, automatically reveal:
  Stage 2 EMA 50; Stage 3 EMA 20; Stage 4 EMA 10; Stage 5 monthly R1.
- Stages unlock in that exact order, one every three seconds; table
  headings stay stable and unrevealed cells show —.
- No five-fold repetition of Yahoo requests. One 2-year daily history
  download per uncached stock group calculates and caches ALL indicators.
- Previous monthly R1 formula/expiry and nine-hour local cache unchanged.
- CSV export may include all legitimate calculated values even when a
  later column has not yet been revealed in the on-screen animation.
- Same full ~219-stock universe, existing paced/rate-limited batches
  and no Yahoo history calls from Streamlit UI thread.

## Scope preserved

- Compact search/filter/source toolbar, rosy light appearance and table.
- Contract-size filters, ascending sort, ₹L toggle, 5%+ CE ratio.
- One-click full-universe ATM straddle scan and Dhan option-chain limits.
- ResultBot, live positions, PIN/TOTP and order logic remain untouched.

## Windows CMD update

Stop Streamlit with Ctrl+C, then run:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

CI is offline/mocked; confirm live Yahoo throttling and final visual on
the owner's laptop after pulling.
