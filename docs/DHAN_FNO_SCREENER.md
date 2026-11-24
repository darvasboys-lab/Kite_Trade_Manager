# Search Stock · NSE Stock F&O contract-value screener

This is Phase 1 of stock discovery inside the separate Windows Payoff Studio.
The original Kite order app and the working ResultBot VM are not modified.

## Windows use

1. Update the DhanPayoffStudio clone after merge: git pull --ff-only origin main
2. Install into the repaired local Python environment using the requirements-dhan-visual.txt file.
3. Launch dhan_visual/app.py through Streamlit on 127.0.0.1:8503.
4. Open Search Stock and click Load NSE F&O stock list + indicative prices.
5. Choose expiry month and search symbol/company, or select min/max contract amount or 5/6/7/8 lakh preset.
6. Sort ascending or descending by estimated contract value and export the filtered table as CSV.

## Official source

- NSE official expiry-specific Permitted lot size CSV: https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
- Contract info: https://www.nseindia.in/static/products-services/equity-derivatives-contract-information
- NSE lot size changes by expiry. Only stock F&O symbols are shown; index derivatives are excluded.
- A public stock-market lot file does not itself prove a particular option strike is tradable; that requires contract master verification later.

## Quote source

- Yahoo Finance .NS NSE equity symbols, with 15-minute bar close and daily-bar fallback.
- Prices are indicative, may be delayed or unavailable, and are NOT broker exchange LTP.
- Price source and bar date/time are retained for each stock.
- Contract value = selected-month stock derivative lot size × latest available equity bar close.
- This is the underlying notional amount, NOT short-call margin or total capital actually needed.
- If NSE download is blocked, manually download the official CSV and upload it; user-provided source is explicitly identified.
- Blank Yahoo quotes mean unknown price / contract value, never ₹0.
- Quotes with unknown value do not participate in numeric filters. An unfiltered table shows them last.
- Cache durations: lot file 4 hours; Yahoo bars 15 minutes. Both have manual Refresh controls.

## Safety

- No broker auth, order placement, option selling, trade triggers, live execution or strategy activation.
- On-demand loading only. Opening Manage Trade does not automatically call NSE or Yahoo.
- Free Yahoo Finance may throttle bulk requests. Missing quotes or failed NSE requests display errors instead of invented data.

## Offline tests

python -m unittest discover -s tests -p test_dhan_screener.py -v
