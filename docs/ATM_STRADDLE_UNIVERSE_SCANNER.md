# All-stock ATM short-straddle screener and compact quote details

## Straddle maths (same expiry, same ATM strike)

Gross ATM straddle LTP premium = ATM CE last price + ATM PE last price.
Premium percentage = gross ATM straddle premium / Dhan option-chain
underlying LTP * 100.

Example CMP ₹100; ATM CE ₹3; ATM PE ₹2.50:
combined premium ₹5.50, premium percentage **5.50%**, not 5%.
If one leg or the underlying LTP is unavailable, the stock is NOT ranked.

Table is sorted descending by numeric percentage, highest first. Also shows
underlying LTP, strike, CE/PE, combined premium, indicative premium/lot,
best-bid percentage (only when both actual bids exist), and exact option expiry.
The percentage is gross potential credit based on LTP, not executable sale
price, expected profit, net yield or margin. Short straddles carry substantial
downside risk and potentially unlimited upside loss.

## Complete universe (not a first-50 patch or ₹7L cash filter)

- Select an existing F&O expiry month (e.g. DEC-26).
- In ATM short-straddle screener, click **Scan all N ATM straddles** once.
- This schedules a single background worker, scanning every listed F&O stock
  regardless of its contract size or whether Yahoo returned a stock price.
- Uses the existing local Dhan Client ID/token, or optional user-configured
  Windows Credential Manager TOTP login. No broker order APIs.
- Attempt one public NSE_FNO segment CSV lookup for exact expiry dates,
  validated by stock symbol and instrument type. If unavailable, fall back
  to Dhan's optionchain/expirylist for the individual stock.
- Then request Dhan optionchain only for that symbol and actual active expiry.
- The existing process-wide one-request-per-3.15-sec rate limiter is preserved
  for BOTH expirylist and option-chain POSTs.
- A full 219-stock scan with known expiries requires at least around 11 minutes
  at Dhan's documented rate. If expiries must be individually checked via API,
  it could exceed 22 minutes. Network delays can add more. The UI shows progress
  and a partial descending leaderboard while it works.
- Stop scan is cooperative after the current request. Resume retries missing
  or failed stocks, keeps valid results, and avoids resubmitting successful ones.
- HTTP 401/403/429 aborts rather than risking a long sequence of rejected calls.
- Rankings are incomplete until all underlyings have been attempted.

## Minimal quote timestamps

- The main cash-stock table hides Quote time and Price source columns.
- Large F&O list provenance and quote/request timestamps are removed from the
  main screen. The table retains the full rupee/lakh display toggle and filters.
- Source details and timestamps remain inside one collapsed expander for audit.
- Full-precision raw cash-stock CSV still includes the original metadata.
- The straddle leaderboard shows a tiny data coverage caption and keeps its
  last snapshot timestamp in a collapsed `Unavailable stocks and quote details`.

## Safety

- Yahoo remains the default cash-stock source. ATM option chains are Dhan-only.
- No 219 API request burst on initial webpage load; one explicit button
  initiates the entire scan. No per-stock clicks, no hidden polling loop.
- No live broker order, holdings, ResultBot VM or payoff calculations altered.
- Tokens, TOTP seeds and PINs are not exported or written to source files.

## Windows CMD update

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Automated tests are offline. Real Dhan option-chain permissions, exact
expiry lookup and runtime throughput need verification on the owner's laptop.
