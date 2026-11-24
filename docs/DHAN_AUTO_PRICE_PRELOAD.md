# Automatic F&O price preloading — full 219-stock universe

## Goal
Loading Search Stock must automatically start both the official F&O lot-size
list and (once a valid local Dhan token exists) the NSE_EQ stock-price snapshot.
Do not ask the user to click Fetch Prices, or to manually load the next 50.

## How it works

- The Streamlit application initializes the F&O stock list in a background worker
  immediately on opening; both tabs are evaluated on initial render.
- Once the list appears, Dhan (default quote provider) automatically looks up
  cash security IDs when needed and sends all eligible F&O stocks through its
  read-only `/v2/marketfeed/ltp` bulk endpoint.
- 219 underlyings fit in one permitted 1000-ID batch. If Dhan omits some prices
  while responding with usable quotes, the worker retries missing once (no polling loop).
- If Dhan credentials were not entered in this local browser session or environment,
  the screen requests them. Active subscription is not itself an access token.
- If the user changes to Yahoo Finance, **all** F&O symbols load without any
  further clicks, in 25-stock background chunks, with table updates as chunks finish.
- The source toggle keeps Dhan and Yahoo separate; quotes are not blended.
- When prior price data exists, the Yahoo request order prioritizes only stocks
  **previously** measured below ₹7,00,000. Unknowns cannot be categorized before price retrieval.
- By default the table is sorted contract value low→high and filtered below ₹7 lakh.
  The All stocks option shows the entire F&O list, with missing prices as N/A.
  While waiting for the very first quote, the unfiltered stock/lot list remains visible.
- Data source, quote timestamp, matched stock count and missing quote count stay visible.
- Users can manually force refresh, but are never required to click for initial population.

## Safety

No new account/order endpoints. No token refresh. No ResultBot VM connection or
trade/portfolio modification. Credentials remain in local Streamlit session RAM,
and are not persisted in Git, the public symbol cache, or exported CSV.

## Windows

Stop the running Streamlit app with Ctrl+C, then:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Tests are all offline/mock-only and never call live market data.
