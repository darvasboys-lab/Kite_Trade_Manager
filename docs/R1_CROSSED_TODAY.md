# Today's Verified Monthly R1 Crossings — top screener panel

## User-visible behaviour

- `↗ Crossed Monthly R1 Today` appears above the full stock screener.
- It automatically scans all NSE F&O stocks eligible for the selected
  expiry month, independent of the normal Search and ₹7-lakh price filter.
- It only begins AFTER the existing daily Yahoo technical history worker
  finishes AND the EMA200→50→20→10→monthly-R1 staged reveal is complete.
- Every qualifying stock appears with its **full existing screener row**:
  clickable NSE TradingView symbol, 15-minute Yahoo CMP, lot size, contract
  value (₹L toggle), EMA200/50/20/10 values/green checks, monthly R1,
  and CE strike at least 5% OTM percentage where the Dhan option scan
  already returned it. No extra Dhan option requests.
- Adds brief 15-minute-crossing candle time and a current status of
  `✅ Above` or `Back below`. A stock that crossed earlier today remains
  listed even if it subsequently fell back under R1.
- All rows appear in a scrollable stock table. There is no hidden 50-stock
  results cap, and the same Auto-fit button applies to the new top table.
- User can click `↻ Recheck` after the current scan completes for a fresh
  paced Yahoo pass. No repeated market-data fetches on ordinary reruns.
- Shows explicit checked/total and unavailable counts while loading; no
  claim of 0 crosses is made when Yahoo bars/R1 history are missing.

## Precise crossing rule

- R1 is the **standard current-calendar-month pivot R1**, computed from
  the PREVIOUS completed month's High, Low and Close as before.
- All timestamps are explicitly converted to Asia/Kolkata and restricted
  to the current IST date and regular NSE 09:15–15:15 15-minute bars.
- Confirm upward piercing only when a bar's actual High is strictly ABOVE
  R1 and that bar's Open or previous 15-minute Close was at/below R1.
- If today's first candle opened above R1, require the previous completed
  trading session's 15:15 candle Close at/below R1 to verify a gap-up
  crossover. If that previous-close evidence is missing, do NOT count a
  stock that simply opens/stays above as a new crossover.
- Stocks that were already above R1 yesterday with no fresh dip and
  recrossing today do NOT appear.
- A bar touching exactly R1 does not count as a crossing. A high above
  R1 that retraces still counts; the status records that it is Back below.
- The candle label is its start time, not the exact exchange trade time.
- Never infer 'crossed today' from a static CMP>R1 tick alone.

## Free Yahoo pacing and safety

- Independent 15-minute OHLC worker: 5-day period, at most 6 NSE symbols
  per call, threads disabled, ≥8 second gap between batches and ≥30s
  backoff after fully unavailable batches.
- A 219-underlying universe may require 37 rate-limited batches; results
  stream into Streamlit without blocking the normal table.
- A session may be stale until the optional Recheck is used, particularly
  during trading hours. Free Yahoo may rate-limit, delay or omit symbols.
- The R1 scan never calls Dhan, places orders or edits account state.
- Missing/invalid/unverified histories stay UNKNOWN, never false negatives.
- Date changes create a fresh day-scoped result set; the panel does not
  display yesterday's crosses as today's.

## Windows CMD update

Stop the existing Streamlit process with Ctrl+C then run each separately:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

All CI tests use mocked Yahoo data. Actual live source coverage, intraday
availability and browser appearance still require verification on laptop.
