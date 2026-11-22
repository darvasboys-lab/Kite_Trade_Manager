"""Local-only update guard. Cannot inspect remote broker GTTs."""
import json
import sys
from pathlib import Path

p = Path(__file__).resolve().parent / 'runtime.json'
if p.exists():
    try:
        state = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        sys.exit(f'UPDATE BLOCKED: cannot read runtime.json: {exc}')
    status = str(state.get('status', '')).upper()
    if status not in ('', 'IDLE', 'STOPPED'):
        sys.exit(f'UPDATE BLOCKED: trading state is {status}. Reconcile in Kite first.')
    if state.get('submitting'):
        sys.exit('UPDATE BLOCKED: uncertain GTT submission. Check Kite.')
    if any(str(g.get('gtt_status', '')).lower() in ('', 'active', 'triggered') for g in state.get('owned', [])):
        sys.exit('UPDATE BLOCKED: tracked GTT is active/triggered/uncertain. Check Kite.')

print('Local state preflight passed. Confirm all remote broker GTTs in Kite before updating.')
