# CCS Phone — experimental native-style web frontend

Stop existing CCS before launch. From the existing Android Debian checkout:
```sh
cd /root/CCS
source .venv/bin/activate
python -m dhan_visual.phone_server
```
Open exactly http://127.0.0.1:8502. No Streamlit frontend is used. Do not expose the server to LAN/internet or run desktop CCS concurrently. Existing Python dependencies still apply. The local server has one unlocked vault per process; lock it in Settings or stop the server when finished. Existing encrypted vault is reused. Official FYERS sample redirect + full URL paste is supported; no biometric or daily auth bypass. No plaintext credential storage or broker order routes. No offline data caching.

This candidate is deliberately not a complete replacement: the native-style frontend supports FYERS vault, explicit batched scan, shortlist/technical details, R1 check, futures, CE/PE option-chain viewing, covered-call accumulation plan and hypothetical trade tracking. Short-straddle and arbitrary multi-leg payoff building, Dhan/public provider parity, historical shortlist persistence, full automatic scanning, full CSV export, automatic callback, and full desktop parity remain outstanding. Use desktop CCS for these. Test 320/360/390/430 px, Android Chrome standalone install, real authorized FYERS connection, scan pacing, missing/stale quotes, every page and desktop regression before merge. No live broker tests have been performed by this change.
