# Payoff Studio — independent Dhan TOTP auto-login and dark terminal UI

## Existing ResultBot implementation checked (read-only)

Repo `darvasboys-lab/resultbot` uses `src/broker/dhan_client.py`
`compute_totp` (RFC 6238, 30 second/6 digits) and `_perform_totp_http_login`
at Dhan's `https://auth.dhan.co/app/generateAccessToken`, with 3
query values: dhanClientId, 6-digit PIN, current TOTP. Its Linux
`src/broker/dhan_token.py` holds one shared VM token using fcntl flock.
**Do not import its VM modules or read ~/.dhan_token from this Windows app.**

## Payoff Studio implementation

- TOTP setup is optional and OFF by default. Enable it only from the local
  dashboard, after acknowledging possible token interaction with ResultBot.
- Keep the existing remembered Client ID in `.dhan_cache/client_settings.json`.
- Provide the 6-digit Dhan PIN and the original 16-character (or longer)
  base32 TOTP setup key locally. Neither ever enters the GitHub repo.
- On Windows, the PIN, setup key and current issued token are saved via
  `keyring` ONLY if the backend is Windows Credential Manager. No plaintext
  secrets or alternative Linux/macOS keyring storage are enabled.
- The original ResultBot TOTP mathematics was adapted using Python's
  standard library (SHA1/HMAC). `keyring` is the only new dependency.
- POST PIN/TOTP over HTTPS to Dhan's officially documented auth endpoint;
  verify token account ID and expiry, then GET /v2/profile to confirm identity.
  Any failure is displayed without token, PIN, raw response or TOTP exposure.
- If the cached token will expire in 25 minutes, request a replacement
  while the Streamlit app remains open. One authentication job at a time,
  and after failure do not retry more often than once every 10 minutes.
- A manually provided fresh token takes precedence over auto-login.
- Disabling deletes the local Windows vault items. A token already issued
  must be revoked separately from Dhan Web if necessary.

## Sensibull-inspired design (not a copy)

- Compact, dark trading terminal header and two page tabs.
- Low-glare graphite surfaces, blue active controls, green/red P&L shading.
- Dense but readable cards, screening filters, numeric columns and chart.
- Mobile responsive CSS with a cleaner chart and optional comparison curves.
- No Sensibull logo, trademarks, CSS, image assets or proprietary trade widgets.

## Limits

- Automatic token renewal runs only while this laptop Streamlit app is open.
- Auto-login has not been tested against the user's live Dhan account. The
  CI test suite uses mocked HTTP and a fake Windows vault.
- On shared Dhan accounts, separately generated tokens can impact other
  applications; this is why setup is explicit opt-in.
- No order placement, holdings writes, portfolio actions or ResultBot changes.

## Windows CMD

Stop dashboard using Ctrl+C, then run separately:
cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m pip install -r requirements-dhan-visual.txt
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

Open 127.0.0.1:8503 > Search Stock > Automatic Dhan access token · TOTP setup.
