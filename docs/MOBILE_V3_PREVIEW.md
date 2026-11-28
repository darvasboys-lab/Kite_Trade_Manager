# CCS Mobile V3 — cached performance preview

Separate branch; the V2 entrypoint and workspace file are unchanged.
V3 reads the existing workspace via its validated store and uses the existing pure
strategy/accumulation functions. There are no broker requests, credential handling,
background scan workers, workspace writes or order APIs in this preview.
Live FYERS refresh, full strategy option-chain editing, saved strategies and feature
parity are not yet migrated. Use V2 for those functions. Saved data must first be
accepted in V2; V3 will not invent quotes. Reload saved data reads disk again.

Install the optional V3 dependencies in the existing virtualenv, then launch:
```sh
.venv/bin/python -m pip install -r requirements-mobile-v3.txt
.venv/bin/python -m dhan_visual.mobile_v3
```
Open http://127.0.0.1:8503. V3 binds only to loopback. Do not expose it publicly.
For an offline check: `python -m unittest discover -s tests -p 'test_mobile_v3.py'`.

## PWA and snapshot controls
Open the same localhost origin (127.0.0.1:8503) in Chrome, then use its Install app / Add to Home screen option if offered. The service worker stores the app shell; the last accepted non-secret snapshot and previous comparison snapshot are kept in browser local storage. Offline browsing requires one successful online load and may be lost when browser storage is cleared. Strategy payoff and stage calculations require the local server. The refresh notification checks for a newer completed V2 snapshot on foreground return or Reload saved data, never via broker polling. Explicit acceptance preserves old data until Update now. Premium changes require the same expiry, CE strike and lot size; filters only narrow saved results. V2 remains necessary for live FYERS scans.
