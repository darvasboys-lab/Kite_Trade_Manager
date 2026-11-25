# FYERS read-only market data in the Covered Call Strategy Builder

This is for the existing **individual FYERS trading account**. Create a separate
**NON-TRADING API app**; do NOT activate a trading app. No FYERS orders can be
placed from the read-only connector, and the existing Dhan account remains separate.

## One active broker at a time

The app has a top-of-page **Active broker connection** selector:

- **Dhan:** Dhan is the only active broker, with its existing saved token/PIN/TOTP
  reused while valid. FYERS broker data requests remain paused.
- **FYERS:** FYERS is the only active broker; Dhan login, token renewal,
  profile checks, funds and option calls are paused. Saved Dhan credentials
  and its cached token are **not deleted**, so switching back doesn't
  require fresh login until the saved Dhan token actually expires.
- **Public only:** Neither broker is contacted. NSE EOD and Yahoo public
  references remain available.

On switching brokers, any already-running broker requests are allowed to finish
(or cancelled when possible) **before** requests for the other broker can start.
This is **local request isolation**, not remote cancellation of a broker-issued
session. Logouts and broker token revocation are independent operations.

When a FYERS non-trading app is already stored in Windows Credential
Manager, a **new app browser session starts with FYERS selected by default**
for option-chain premiums, including 5%+ OTM CE and ATM straddle data.
This local vault check does not log in to either broker. If the daily FYERS
token has expired, reauthorize from Settings; the dashboard must not use
stale premiums. An explicit manual switch to Dhan or Public only is respected.

On **◎ OTM CE Home**, **ATM Straddle Total (₹)** is the actual premium
**per share = ATM CE LTP + ATM PE LTP** for the selected expiry and the
same stock/strike in the same FYERS option-chain response. It is not a
lot-sized value, a percentage return, or an executable quote. Empty cells
mean valid matched option legs are unavailable, not zero premium.
No additional FYERS request is sent for the Home column: it uses the same
paced options scan as the Market Screener.

For first FYERS authorization, switch the selector to **FYERS**, then open
**Settings → FYERS** and enter the App ID and App Secret. Do not enter your
Dhan login again. The app will never ask for both broker credentials together.

## First-time FYERS setup (on your own Windows PC)

1. Sign in at https://fyers.in/web/api-dashboard/user-apps
2. Click **Create non-trading app**.
3. App Name: **Covered Call Strategy Builder**
4. Redirect URL: `https://trade.fyers.in/api-login/redirect-uri/index.html`
5. Leave optional webhook details blank, accept the FYERS terms, and Create.
6. Copy your App ID (ending in `-100`) and App Secret into **our app's**
   **Settings → FYERS · read-only market data**. Never share them via chat,
   GitHub, screenshots or an issue report.
7. Click **Save in Windows Credential Manager** (optional but recommended).
   Both values stay on your Windows user account, not in repository files.
8. Click **Begin FYERS login** → **Open secure FYERS login**.
9. Complete FYERS login and **copy the entire redirected browser URL** from
   the FYERS redirect page, including `auth_code` and `state`.
10. Paste that URL into the **password-masked** field in our local Settings
    and click **Connect FYERS market data**. The authorization state is checked
    before exchanging the code for a token.
11. Choose **FYERS** as cash quotes and CE source if not already selected.
    Option chains for your selected monthly F&O expiry load gradually and
    use **only genuine FYERS stock spot, CE and PE prices**.
12. Click **Test FYERS TCS quote (read-only)** for a real read-only check.

FYERS API tokens expire at **6:30 AM IST daily**. Log in again each day
through the Settings screen. Logging out of the regular FYERS website alone
does not automatically revoke an already-issued API access token. Use
**Disconnect and forget** locally, and revoke access through FYERS if needed.

### What each provider does

- **FYERS:** NSE cash stock quotes, same-snapshot first listed +5% CE premium
  percentage, ATM CE+PE straddles, daily 200/50/20/10 EMA, previous completed
  month's R1 and current-day verified 15-minute R1 crossings.
- **Dhan:** account profile/funds stay connected. Its data-only quote requests
  remain blocked if you don't have Dhan's paid market data plan.
- **NSE EOD:** a separate free, dated last-close fallback. Do not treat its
  option close as an executable market quote.
- **Yahoo:** existing optional fallback when independently selected.

The FYERS Standard plan (revised 16 October 2026) currently allows 5 data
requests/sec, 50/min, 5,000/day and 50 cash symbols/quotes request.
We pace all FYERS REST data requests to at least 1.5 seconds apart.
One full-universe option scan and indicator scan take time; do not loop them
rapidly. If an option is absent, it stays blank—no invented premium.

## Update and launch on Windows CMD

From the actual local Git checkout (replace the path if your folder differs):

```bat
cd /d "%USERPROFILE%\Desktop\CoveredCallScreener_Git"
git pull --ff-only origin main
launch_dhan.bat
```

App: http://127.0.0.1:8502 — runs on **localhost**, not a public server.

**No live trading authorization** is part of this feature. Trading requires a
separately reviewed and explicitly approved order-execution implementation.

Official FYERS docs:

- https://fyers.in/api-docs/v3/getting-started/create-an-app/individual-app/
- https://fyers.in/api-docs/v3/api/authentication/individual-app/authentication-flow/
- https://fyers.in/api-docs/v3/api/market-data/option-chain/
- https://fyers.in/api-docs/v3/api/market-data/historical-data/
