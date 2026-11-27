# CCS mobile workspace (Android / Termux)
 
- Existing Windows/desktop features, lazy page navigation, rate limits, read-only
  broker isolation and scanner logic are retained.
- Mobile viewport uses wrapped navigation, stacked controls, larger touch targets,
  and responsive chart/table containers.
- On the selective Home, Market Screener and futures results choose Mobile cards
  or Full table. Cards are paginated (8 per page), reuse the same snapshot, show
  all extra fields in an expander and retain TradingView links. Use existing
  Build strategy controls below Home cards; Full table retains cell actions.
- FYERS Android/Linux: opt in to the encrypted mobile vault described below, or use temporary browser-session credentials.
  Begin login, open the link or copy it from the fallback, open a new Chrome tab,
  and paste the complete returned URL into the original CCS tab. OAuth state
  validation is unchanged. No plaintext credential fallback is introduced.
- Stop the server before updating. From Termux: proot-distro login debian, then
  cd /root/CCS and git pull --ff-only origin main. Relaunch the existing shortcut.
- Device acceptance: test 360/390/430px portrait, landscape, theme toggle, each
  workspace, FYERS login link and state rejection, scrollable full tables, cards,
  filters, strategy actions, charts, CSV export and no offscreen scan requests.
  Android hardware/browser behavior requires on-device verification.

## Mobile FYERS credential vault

On Android/Linux, select FYERS in Settings, enter your own App ID and Secret,
and choose Save FYERS credentials on this phone. Set a strong passphrase.
After each new CCS browser session, unlock once; credentials are prefilled
without showing the secret and a matching unexpired access token is reused.
Daily FYERS authorization still applies. The encrypted file is located under
~/.ccs-private/fyers.vault (private directory 0700, file 0600), not in Git,
workspace exports or shared Android storage. PBKDF2-HMAC-SHA256 (600,000
iterations, random salt) and authenticated Fernet encryption are used. The
passphrase/key is never persisted; this is not an Android hardware-backed
Keystore. Keep CCS bound to localhost. Windows Credential Manager is unchanged.
Forgetting the passphrase requires removing/recreating the local vault; no
password recovery. Dhan PIN/TOTP storage remains Windows-only.
