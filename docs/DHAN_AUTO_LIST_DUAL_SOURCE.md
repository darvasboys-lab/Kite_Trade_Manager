# Search Stock V3: automatic F&O list, price-source switch

The user should never have to download or upload the exchange instrument list.

## Startup

- Search Stock automatically starts loading the public F&O universe in a worker and allows the dashboard to remain responsive.
- A valid local cached list is used immediately. Cold start tries Dhan NSE_EQ and NSE official lots, NSE_EQ and NSE_FNO segments, and finally Dhan public detailed master CSV automatically.
- A previous local cache within 48 hours may be used with an explicit stale label.
- Only symbols, expiry-month lots and public IDs are saved in .dhan_cache. No token.
- If all external sources are offline, a Retry button appears, not an upload control.

## Prices

- Dhan / Yahoo Finance switch is visible; Dhan is the default.
- Dhan price requests are explicit read-only POST marketfeed/ltp snapshots, with local credentials. No orders.
- Yahoo is the manually selected free backup: 50 stocks, 100 stocks or All (slow). Never blend Yahoo and Dhan quotes.
- Missing values remain N/A and numeric filters exclude unknown prices.
- Contract value = stock price x expiry-specific lot size, and is not the required short-call margin.

## Simple payoff

- Show one line by default: current shares at expiry, preserving uncovered-call risk.
- Switch to hypothetical after-all-buys expiry view to see conditional covered payoff.
- The comparison line, modelled today curve and target guide are optional under collapsed chart settings.
- Current closeout card still uses editable stock BID and short-call buyback ASK, separate from expiry curve.

## Windows CMD, run each separately

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m pip install -r requirements-dhan-visual.txt
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

External API availability still depends on the network. No instrument IDs or lot sizes are fabricated.
No ResultBot VM, order, risk, protection or Kite trading changes.
