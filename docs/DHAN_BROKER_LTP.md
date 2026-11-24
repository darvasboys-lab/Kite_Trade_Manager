# Dhan read-only F&O screener

## Data sources and exact contract calculation

- Dhan detailed instrument master: https://images.dhan.co/api-data/api-scrip-master-detailed.csv
- NSE stock F&O derivative rows: exchange NSE, derivatives segment, OPTSTK/FUTSTK type.
- Only underlyings with verified NSE cash equity security IDs (NSE_EQ) are included.
- Lot size comes from the corresponding derivative contract month in the Dhan detailed master.
- Price comes from POST https://api.dhan.co/v2/marketfeed/ltp with NSE_EQ IDs.
- Contract value (notional) = latest returned cash stock LTP × derivative lot size.
- Dhan marketfeed/ltp accepts up to 1,000 IDs per request and rate limit 1 request/s.
- A received LTP snapshot has request completion time, NOT necessarily last-exchange-trade timestamp.
- Missing data is unknown; no Yahoo fallback. Size filters exclude unknown price rows.

## Laptop setup (CMD, one command at a time)

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m pip install --no-cache-dir -r requirements-dhan-visual.txt
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Open http://127.0.0.1:8503 in the local laptop browser.

1. Open Search Stock and click Load stock F&O universe from Dhan.
2. Expand Dhan market-data credentials and enter your **own** Dhan Client ID and access token locally.
3. Click Fetch / refresh Dhan stock LTPs.
4. Select expiry, search symbols, and sort/filter by lot × stock LTP, with ₹5–8 lakh quick presets.
5. Download filtered CSV. Price refresh is manual, not background/live streaming.

## Security and scope

- This tool does not read trading account holdings, positions, orders or funds.
- Dhan credentials are transmitted ONLY to marketfeed/ltp over HTTPS and kept in local Streamlit session RAM.
- Never paste broker tokens into ChatGPT or GitHub or screenshot them.
- The original ResultBot VM must remain untouched. Never mount its session tokens or databases.
- The local Streamlit server listens on 127.0.0.1 only.
- No buy/sell execution routes are added, and Dhan LTP is not yet used for trade triggers.
- Stock LTP is not an executable options quote and is not margin cost.
- If the public Dhan master download is blocked, download the same master from Dhan and upload the CSV manually.

## Tests

python -m unittest discover -s tests -v
