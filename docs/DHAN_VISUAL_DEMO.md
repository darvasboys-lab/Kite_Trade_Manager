# Dhan Covered Call — Windows strategy visualization (preview)

This separate module is a **read-only DEMO** within the existing Kite Trade Manager repository. The original Kite application in app.py, worker.py, logic.py and all its order code are untouched.

## Launch on Windows

1. Clone the development branch into a **different directory** (do not replace your current CoveredCallBot or Kite project).
2. Double-click run_dhan_preview.bat from the cloned folder, or run it from Windows CMD. It creates its own .venv and opens a local-only Streamlit process on http://127.0.0.1:8503.
3. Choose **Manage Trade & Strategy Charts**. The defaults are an **illustrative** ₹1,000 original manual buy (100 shares), 500-share ₹1,200 sold call, ₹10,000 premium. Change Automatic buying batches to see recalculation.
4. Run offline tests: py -m unittest discover -s tests -p "test_dhan_visual.py" -v.

## Visuals

- Accumulated equity share count versus stock price, with batch markers, short-call coverage, spot indicator and call strike.
- Combined **indicative closeout P&L** = owned shares × hypothetical stock BID − simulated actual share cost + original call credit − contract shares × hypothetical buyback ASK − estimated closeout costs.
- Conditional **expiry P&L curve**, assuming all purchases complete at the planned trigger prices; beyond the call strike, fully covered stock upside offsets short-call intrinsic payoff.
- Profit alert threshold = 50% of conditional projected payoff at the strike (after user-entered costs).
- Order-free table listing first manual buy and all automatic stages, exact share quantities, trigger prices, and future capital need.

## Critical non-live constraints

- Values are not imported from Dhan. No actual holdings, call premiums, filled quantities, quotes, risk, funds or option prices are verified.
- There is deliberately NO broker login, scheduler, order placement, or live trading permission in this UI. The existing ResultBot VM and old Kite main branch are untouched.
- The new Dhan backend must reuse the already-copied ResultBot Python broker client, instrument resolver, order journal and emergency STOP safeguards after auditing its current source and constructing a separate isolated runtime.
- The new BUY strategy must establish an immutable first-manual-batch average from transaction-level Dhan fills, not a blended present-day holding average, before activation.
- While shares held are below short-call shares, the call is partially uncovered and subject to potentially severe losses, margin calls and expiry delivery risk.
- The hypothetical expiry curve is a conditional scenario only, not the actual position payoff before planned buys fill; quote- and fill-verified current P&L is a separate calculation.
- No live pricing or option chain exists until Dhan's feed and instrument master are wired in and independently validated.
- No Kite GTT, Kite account, or ResultBot VM changes are made by this module.
