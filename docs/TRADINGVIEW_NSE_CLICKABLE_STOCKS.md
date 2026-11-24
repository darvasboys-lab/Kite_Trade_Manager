# TradingView stock chart links — Covered Call Strategy Builder

## Click any stock ticker to open NSE chart on TradingView

- In the Search Stock table, the visible **Stock** field becomes a clickable
  hyperlink with the original ticker as its label (e.g., TCS, SBIN, M&M).
- The ATM short-straddle ranking also displays clickable Stock labels.
- Clicking opens TradingView's browser chart for that specific NSE symbol:
  https://www.tradingview.com/chart/?symbol=NSE%3ATCS
- Special characters are safely URL-encoded in the chart query, e.g. M&M
  becomes `?symbol=NSE%3AM%26M` while the label still displays `M&M`.
- Link addresses are generated locally and always use the fixed
  `https://www.tradingview.com/chart/` destination; the NSE symbol is
  validated against the existing allowlist in the F&O screener.

## Scope / limitations

- Only a STOCK CHART opens. A private or custom TradingView Pine Script is
  NOT installed or injected by this feature. If an indicator is saved in
  the user's TradingView default chart layout it may already be present.
  A specific saved layout or Pine Script requires its user's explicit
  TradingView chart/layout reference; never invent a private script URL.
- The internal raw `Symbol` and `Stock` remain unchanged for calculations,
  price-source lookup, sort/filter, CSV exports and offline tests.
- Streamlit shows a new LinkColumn labeled Stock and hides the duplicate
  internal raw stock column, so the previous Company-name removal is intact.
- All links are passive; no TradingView HTTP call is made until clicking.
- No order entry or broker data transfer; Yahoo/Dhan permissions unchanged.
- One-click Auto-fit also applies to the linked labels.
- Current F&O instrument mapping is to NSE cash shares, not derivatives
  and not a particular option strike.

## Windows CMD update

Stop the running Streamlit app using Ctrl+C, then run separately:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

CI validates link building, escaping, label extraction, blocked arbitrary
URLs, no data mutations, and Streamlit AppTest rendering. A real browser
click and TradingView account/chart layout may still need local testing.
