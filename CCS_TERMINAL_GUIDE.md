# CCS Terminal 1.0.0

Read-only local analysis app, not an order execution system.

## Windows setup
From the existing repository run `git pull --ff-only origin main` while the app is stopped, then run `Install_CCS_Shortcuts.bat` once. Double-click CCS Terminal on the desktop. Close the server window before using Update CCS Terminal. The updater requires a clean main checkout and the expected origin; it never resets or overwrites local changes.

## Daily workflow
- Settings: enable **Remember preferences and save latest shortlist automatically** if desired. Credentials remain in the existing Windows Credential Manager.
- Home: apply expiry, capital, premium and R1 filters. The existing 35-chain / minute scanner remains explicit and bounded. Save a named preset or historical shortlist. The historical view is not a live quote cache and never feeds the scanner.
- Strategy Builder: select an NSE stock independently, or launch from Home. Select a strike and use Buy/Sell CE/PE quick-add buttons; edit lots and premiums, set estimated charges, save a named strategy or export CSV. Reopen saved current contracts from the independent tab. Expired or changed lots are refused.
- Settings: API Control Centre shows process-local logical FYERS requests, Yahoo download batches and rate-limit events. Existing shared FYERS pacing remains unchanged. Export/restore a non-secret JSON workspace backup. Restore requires explicit confirmation and app restart to apply preferences.
- Payoff is selected-expiry only, with other-expiry legs excluded; not a calendar spread valuation or margin estimate.

## Release verification
Offline CI does not establish live FYERS connectivity, native browser styling or actual Windows shortcut behavior. Verify locally before declaring the release stable. No background update, automatic Git merge or order calls.
