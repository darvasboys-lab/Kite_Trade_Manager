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
