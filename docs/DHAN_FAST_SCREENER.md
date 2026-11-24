# Fast F&O screener — 2026-11-24

## Why initial load was slow

The previous build downloaded and parsed Dhan's entire all-exchange detailed
instrument master, although the screener only needs the NSE cash-equity IDs
and a small monthly NSE stock F&O lot table. That work was repeated after
Python restarts. The new build avoids the global file in normal operation.

## Faster pipeline

1. Click Load stock F&O universe from Dhan.
2. The local public-metadata cache is reused immediately if less than 20 hours old.
3. Otherwise download NSE_EQ equity IDs from Dhan segment-only endpoint.
4. Join them with the small official NSE F&O permitted lots CSV.
5. If NSE lots are blocked/incomplete, download Dhan's NSE_FNO *segment-only* CSV.
6. Persist the verified reduced list (symbol, lot, expiry month and public cash
   security ID only) to .dhan_cache/stock_fno_universe_v2.json.
7. If the source is unavailable, an existing 20–48-hour-old cache is visibly
   marked STALE and is never used for order execution (there is no trading UI).

## Loading stock prices

Dhan supports 1000 equity IDs per LTP request with a 1 request/second rate
limit. For the stock F&O universe, All stocks usually requires just one bulk
request and gives a complete contract-size ranking.

The optional Next 100 / Next 250 modes load small patches to diagnose slow
connections; existing quotes from earlier patches are retained in local
Streamlit session RAM. When only a subset has prices, the UI explicitly states
that contract-value filtering is incomplete until the full universe is quoted.

## Windows

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m pip install -r requirements-dhan-visual.txt
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

New requirements are not added. No Dhan token is written to disk,
and there are no trading endpoints or automated broker workers.

## Testing

python -m unittest discover -s tests -v
