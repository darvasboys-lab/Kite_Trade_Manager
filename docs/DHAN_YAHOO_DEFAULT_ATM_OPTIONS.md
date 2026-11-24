# Search Stock — Yahoo default, ₹L display, optional ATM CE/PE

## Price data on page load

- Yahoo Finance is the default price source. The F&O universe loads from official
  NSE lot-size records, then Yahoo loads the full list progressively in 25-stock
  background chunks. This is indicative historical bar-close data, which can be
  delayed or rate limited; missing prices remain N/A, never invented.
- Dhan broker LTP remains selectable and uses locally supplied token or existing
  opt-in TOTP auto-login. ResultBot VM and token files are never touched.

## Contract-value display

- Default toggle: `Display contract values in lakhs (₹L)` = ON.
- Example: ₹270500 is displayed as ₹2.7L. Toggle OFF for full-rupee amount.
- The underlying numeric sort, below-₹7-lakh filter, expiry lot and CSV exports
  remain full precision INR. The compact view is still a numeric column, not text.
- The existing low-to-high contract-size sort is retained and does not compare
  formatted strings lexicographically.

## ATM option values (on-demand only)

- The collapsed `ATM options · CE / PE` expander is displayed after the table.
- Select ONE stock from the full available underlying list, then click
  `Load ATM CE / PE`. The page makes **zero option-chain HTTP calls before click**.
- Both Dhan market-data endpoints are read-only:
  `POST /v2/optionchain/expirylist` and `POST /v2/optionchain`.
- If the public stock lot table lacks a verified NSE cash security ID, resolve
  it from a Dhan instrument file only on clicking; never invent an ID.
- Select the nearest still-active actual expiry date within the chosen F&O month.
- Select ATM strike using Dhan's option-chain `data.last_price` and nearest
  available strike (tie goes to the lower strike). Do not use stale Yahoo closes
  or another month's options to choose the ATM strike.
- Show CE and PE LTP/share, best bid and ask, and indicative LTP × F&O lot size.
  A missing or zero price displays N/A, not ₹0.
- Respect Dhan's three-second option-chain rate limit, with an independent
  serialized limiter. Nothing is prefetched for all 219 stocks.
- Dhan option chain requires Dhan authentication even when Yahoo is the stock
  quote source. Local access token entry is allowed only within the collapsed
  option panel; existing opt-in Windows TOTP vault can be used on the user's
  explicit Load click if a cached valid token is unavailable.
- No trade, order, margin calculation, ResultBot integration or background
  option-chain sweeps are introduced.

## Windows update (run each command individually)

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

## Tests

All broker interaction is mocked in CI, including option-chain expiry/strike
parsing, missing data, API authorization failures, and UI no-HTTP-until-click.
Live Dhan option data and Yahoo completeness must be verified on owner's PC.
