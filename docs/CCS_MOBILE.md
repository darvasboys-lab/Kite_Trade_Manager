# CCS mobile workspace (Android / Termux)
 
- Existing Windows/desktop features, lazy page navigation, rate limits, read-only
  broker isolation and scanner logic are retained.
- Mobile viewport uses wrapped navigation, stacked controls, larger touch targets,
  and responsive chart/table containers.
- On the selective Home, Market Screener and futures results choose Mobile cards
  or Full table. Cards are paginated (8 per page), reuse the same snapshot, show
  all extra fields in an expander and retain TradingView links. Use existing
  Build strategy controls below Home cards; Full table retains cell actions.
- FYERS Android/Linux: skip Windows-only storage; use browser-session credentials.
  Begin login, open the link or copy it from the fallback, open a new Chrome tab,
  and paste the complete returned URL into the original CCS tab. OAuth state
  validation is unchanged. No plaintext credential fallback is introduced.
- Stop the server before updating. From Termux: proot-distro login debian, then
  cd /root/CCS and git pull --ff-only origin main. Relaunch the existing shortcut.
- Device acceptance: test 360/390/430px portrait, landscape, theme toggle, each
  workspace, FYERS login link and state rejection, scrollable full tables, cards,
  filters, strategy actions, charts, CSV export and no offscreen scan requests.
  Android hardware/browser behavior requires on-device verification.
