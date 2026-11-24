# Covered Call Strategy Builder — EMA 200/50/20/10 and monthly R1

## Renamed application
The local Streamlit window and header now say **Covered Call Strategy Builder**.
Executable path stays `dhan_visual/app.py`; no change to command or project directory.

## Automatic, gradual Yahoo historical-indicator calculation
- After the normal Yahoo stock-price preload finishes, the page automatically
  begins its own 2-year, 1-day Yahoo history pass for the FULL F&O universe
  (typically 219 stocks), regardless of the selected ₹7 lakh cash filter.
- Request at most SIX symbols per `yfinance.download()` call with threads disabled.
- Wait at least six seconds between normal batches, and back off thirty seconds
  when a batch has no valid data. Do not repeatedly retry failed stocks in
  the same pass. Never force the user to load another 50 manually.
- Cache only public computed indicator values for up to nine hours under
  `.dhan_cache/yahoo_technical.json`, already gitignored; never cache credentials.
- The original Yahoo intraday stock prices, Dhan read-only alternate quotes,
  full-straddle scan, TOTP and covered-call calculations are not changed.
- When Yahoo rate-limits/errors, show — in the corresponding cell instead of
  fabricating technical levels or a green tick.

## Table column definitions
- EMA 200: EMA of 200 actual daily closes, `ewm(span=200,adjust=False,
  min_periods=200)`. EMA 50/20/10 follow identical rules.
- A missing indicator history value shows — until available.
- **Current month's standard Pivot R1** is calculated from the PREVIOUS
  COMPLETED calendar month's unadjusted High, Low and final Close:
  P=(H+L+C)/3, R1=2P-L.
- Data is from Yahoo unadjusted daily stock OHLC; it is not necessarily
  identical to TradingView's adjusted chart settings or another broker feed.
- Example for previous month H=120, L=80, C=100: Pivot=100, R1=120.
- Main screener uses its DISPLAYED CMP (Yahoo latest intraday bar if available,
  or Dhan LTP when source switched) to compare against each Yahoo level.
- CMP strictly above EMA/R1 shows ✓ followed by numerical level (e.g.
  ✓ ₹140.21). CMP at or below shows ✕ with numerical level. When no
  displayed CMP exists, show — alongside the level; no false passes.
- No fill-forward of missing daily closes. EMA 200 only appears when 200
  actual positive closing bars are available.
- All five indicator columns appear alongside each stock in the original
  numeric, ascending contract-value table; compact rupee/lakh toggle remains.
- CSV export includes existing full-precision contract values, quote metadata,
  the five levels/checks and an 'Indicators as of' date.

## Key performance / safety notes
- Yahoo is a public, unofficial, sometimes rate-limited source. The provider
  does not guarantee stable throughput; progress may be slow/incomplete.
- Historical daily levels may lag the current bar during market hours. Source
  details remain under collapsed metadata rather than filling the visible table.
- No broker order placement, Telegram, ResultBot VM, or live position changes.

## Update on Windows CMD
Stop Streamlit using Ctrl+C first:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Offline CI mocks all Yahoo calls and checks full 219-stock coverage and the
previous-month R1 formula. Actual download success requires laptop verification.
