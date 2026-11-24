# Compact strategy screener and 5%-out-of-money CE premium

## Toolbar

- Search, sort, contract-value range, and ₹L display toggle now live in compact
  right-side popover controls above the stock table; visible F&O expiry stays.
- Quote-source selection and both Refresh actions are tucked into the Source
  popover. Previous Streamlit control keys and filtering semantics stay intact.
- Removed the old four-metric row, large Filter & Sort section, separate
  quote-source header and the redundant F&O-list refresh row above the table.
- A slim tinted status strip indicates universe, price coverage and selection.
- Soft white/rose micro-card styling, compact spacing and high-contrast text
  preserve the light theme and reduce scrolling on Windows and iPhone.

## New numeric column: CE strike >= 5% above CMP

Use the existing selected-expiry all-stock ATM straddle scanner. Dhan option
chain replies include ALL strikes for the underlying and chosen expiry, so
one existing request supplies both ATM CE+PE and this new OTM CE measure.
Do NOT run a second chain request for 219 stocks.

Exact definition (using ONE Dhan option-chain snapshot):
  reference CMP = Dhan optionchain.data.last_price;
  target strike = reference CMP * 1.05;
  selected CE = SMALLEST ACTUAL listed CE strike >= target strike;
  CE >=+5% percentage = CE last_price / reference CMP * 100.

Example: Dhan CMP ₹100, 105-strike CE LTP ₹2 => 2.00%.
If 105 is not listed, choose the next REAL strike above 105, e.g. 110.
Do not round CMP*1.05 to a fictional listed strike, do not substitute a
below-target strike, and do not treat a zero/missing CE LTP as 0%.

- Main cash-screen table has ONE sortable numeric `5%+ CE premium (%)`
  column, blank while no valid Dhan option-chain snapshot exists.
- The actual CE strike and Dhan CMP denominator also appear in the
  full-precision stock CSV and additional columns in the straddle results.
- This ratio uses Dhan's underlying LTP, even if the main cash-stock table
  displays a delayed Yahoo CMP, to avoid mismatched timestamps/strike basis.
- During the single user-triggered `Scan all N ATM straddles`, the background
  worker progressively updates both the ATM straddle ranking and 5%+ CE
  values. The scanner retains its 3.15-second rate limiter, Stop/Resume,
  read-only API contracts, and full F&O universe coverage.
- If the ATM put price is missing, a valid 5%+ CE may still populate.
- No option-chain requests occur on initial page load; a valid Dhan token
  and the existing `Scan all` action are needed to fill this column.

IMPORTANT: CE premium as % of underlying CMP is GROSS indicative premium,
not net profit, achievable sale proceeds, margin-based return or guaranteed
yield. The nearest available strike can be MORE than 5% above CMP.

## Windows update

Stop the existing app with Ctrl+C, then:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Live option-market access still requires laptop/Dhan authentication and
must not be assumed from mocked CI responses.
