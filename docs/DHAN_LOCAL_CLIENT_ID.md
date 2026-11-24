# Local Dhan Client ID — remembered, access token never persisted

The Payoff Studio remembers the owner's Dhan Client ID automatically when a valid
numeric ID is entered in Search Stock. After Streamlit restarts on the same PC,
the ID is loaded from `.dhan_cache/client_settings.json`. This directory is
already ignored by Git and contains only public instrument cache metadata and
the Client ID, never broker tokens.

Access tokens have a maximum lifetime of 24 hours, measured from generation,
not necessarily reset at midnight. The app does not renew a token or generate
new tokens; use the current token from DhanHQ when the old token expires.

Dhan officially documents RenewToken for still-active web-generated tokens,
and separate API key/secret + user consent or PIN/TOTP authentication flows.
Automatic renewal must be developed and reviewed separately if required.

Do not paste tokens into ChatGPT, screenshots or GitHub. If a token was shared,
revoke/regenerate it through Dhan before using this studio.

## Windows update

cd /d "%USERPROFILE%\Desktop\DhanPayoffStudio"
git pull --ff-only origin main
.venv_fresh\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503

The app will save a valid ID without requiring a Save button. It still requests
all eligible stock prices in one automatic read-only Dhan LTP batch as soon as
a current token is entered. No broker order code or ResultBot VM is touched.
