"""Offline security and token-lifecycle tests; NEVER contact Dhan or ResultBot."""
import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from dhan_visual.auto_auth import (
    AutoAuthError, AutoAccess, SERVICE, INDIA_ZONE,
    _parse_expiry, _parse_token_response,
    generate_totp, normalize_totp_secret, validate_pin,
    forget_credentials, is_configured, read_cached_token,
    request_new_access, load_or_generate, store_credentials,
)

CID = "1234567890"
SEED = base64.b32encode(b"12345678901234567890").decode("ascii")


class FakeWindowsVault:
    def __init__(self):
        self.items = {}
    def get_password(self, service, username):
        return self.items.get((service, username))
    def set_password(self, service, username, value):
        self.items[(service, username)] = value
    def delete_password(self, service, username):
        self.items.pop((service, username), None)


class AutoAuthTests(unittest.TestCase):
    def test_standard_resultbot_compatible_totp(self):
        self.assertEqual(
            normalize_totp_secret("jbsw y3dp ehpk 3pxp"), "JBSWY3DPEHPK3PXP"
        )
        # RFC6238 test vector reduced from 8 to 6 digits.
        self.assertEqual(generate_totp(SEED, at=59), "287082")
        with self.assertRaises(AutoAuthError):
            normalize_totp_secret("123456")
        with self.assertRaises(AutoAuthError):
            normalize_totp_secret("eyJhbGciOiJIUzUxMiJ9.foo.bar")

    def test_six_digit_pin_validation(self):
        self.assertEqual(validate_pin("012345"), "012345")
        for bad in ("1234", "1234567", "ABC123", "123 456", "0000000000"):
            with self.assertRaises(AutoAuthError):
                validate_pin(bad)

    def test_ist_token_expiry_is_timezone_aware_and_bound_to_client(self):
        future = datetime.now(INDIA_ZONE) + timedelta(hours=20)
        expiry = future.strftime("%Y-%m-%dT%H:%M:%S")
        actual = _parse_expiry(expiry)
        self.assertEqual(actual.utcoffset(), timedelta(0))
        self.assertAlmostEqual(
            (actual - future.astimezone(timezone.utc)).total_seconds(), 0, delta=1
        )
        with self.assertRaisesRegex(AutoAuthError, "Client ID"):
            _parse_token_response({
                "dhanClientId": "9999999999",
                "accessToken": "MOCK_TOKEN_FOR_UNIT_TEST_" * 3,
                "expiryTime": expiry,
            }, CID)

    def test_windows_vault_only_storage_and_deletion(self):
        vault = FakeWindowsVault()
        with patch("dhan_visual.auto_auth._keyring", return_value=vault):
            store_credentials(CID, "123456", "JBSWY3DPEHPK3PXP")
            self.assertTrue(is_configured(CID))
            obj = json.loads(vault.get_password(SERVICE, CID + ":credentials"))
            self.assertEqual(obj["pin"], "123456")
            self.assertEqual(obj["totp_secret"], "JBSWY3DPEHPK3PXP")
            self.assertNotIn("accessToken", obj)
            forget_credentials(CID)
            self.assertFalse(is_configured(CID))

    def test_refuses_plaintext_fallback_when_windows_vault_is_unavailable(self):
        with patch("dhan_visual.auto_auth.sys.platform", "linux"):
            with self.assertRaisesRegex(AutoAuthError, "Windows Credential Manager"):
                store_credentials(CID, "123456", "JBSWY3DPEHPK3PXP")

    def test_live_protocol_is_mocked_and_profile_identity_verified(self):
        future = datetime.now(INDIA_ZONE) + timedelta(hours=20)
        expiry = future.strftime("%Y-%m-%dT%H:%M:%S")
        token = "MOCK_LONG_JWT_BUT_NOT_A_REAL_TOKEN_" * 3
        session = MagicMock()
        post = session.post.return_value
        post.status_code = 200
        post.json.return_value = {
            "dhanClientId": CID, "accessToken": token, "expiryTime": expiry
        }
        profile = session.get.return_value
        profile.status_code = 200
        profile.json.return_value = {"dhanClientId": CID, "dataPlan": "Active"}
        with patch("dhan_visual.auto_auth.generate_totp", return_value="123456"):
            result = request_new_access(CID, "123456", SEED, session=session)
        self.assertEqual(result.token, token)
        self.assertTrue(result.usable())
        session.post.assert_called_once()
        self.assertEqual(
            session.post.call_args.args[0],
            "https://auth.dhan.co/app/generateAccessToken"
        )
        request = session.post.call_args.kwargs
        self.assertEqual(
            request["params"], {
                "dhanClientId": CID, "pin": "123456", "totp": "123456"
            }
        )
        self.assertFalse(request["allow_redirects"])
        session.get.assert_called_once()
        self.assertEqual(
            session.get.call_args.args[0], "https://api.dhan.co/v2/profile"
        )
        self.assertEqual(
            session.get.call_args.kwargs["headers"]["access-token"], token
        )

    def test_bad_profile_id_never_saves_token(self):
        token = "MOCK_LONG_JWT_BUT_NOT_A_REAL_TOKEN_" * 3
        session = MagicMock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {
            "dhanClientId": CID, "accessToken": token,
            "expiryTime": (datetime.now(INDIA_ZONE) + timedelta(hours=20)).isoformat()
        }
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {"dhanClientId": "9999999999"}
        with patch("dhan_visual.auto_auth.generate_totp", return_value="123456"):
            with self.assertRaisesRegex(AutoAuthError, "does not match"):
                request_new_access(CID, "123456", SEED, session=session)

    def test_http_error_message_never_exposes_pin_or_totp(self):
        session = MagicMock()
        session.post.return_value.status_code = 401
        with patch("dhan_visual.auto_auth.generate_totp", return_value="654321"):
            with self.assertRaises(AutoAuthError) as ctx:
                request_new_access(CID, "123456", SEED, session=session)
        self.assertNotIn("654321", str(ctx.exception))
        self.assertNotIn("123456", str(ctx.exception))
        self.assertIn("401", str(ctx.exception))

    def test_valid_cached_token_prevents_repeat_authentication(self):
        vault = FakeWindowsVault()
        expiry = datetime.now(timezone.utc) + timedelta(hours=20)
        with patch("dhan_visual.auto_auth._keyring", return_value=vault):
            store_credentials(CID, "123456", SEED)
            vault.set_password(
                SERVICE, CID + ":token",
                json.dumps({
                    "version": 1, "token": "MOCK_SECURE_CACHED_TOKEN_" * 3,
                    "expires": expiry.isoformat(),
                })
            )
            cached = read_cached_token(CID)
            self.assertIsNotNone(cached)
            with patch(
                "dhan_visual.auto_auth.request_new_access",
                side_effect=AssertionError("Unexpected TOTP network auth"),
            ):
                second = load_or_generate(CID)
            self.assertEqual(second.token, cached.token)

    def test_ui_is_read_only_secrets_not_in_source(self):
        root = Path(__file__).resolve().parents[1]
        ui = (root / "dhan_visual" / "screener_ui.py").read_text(encoding="utf-8")
        auth = (root / "dhan_visual" / "auto_auth.py").read_text(encoding="utf-8")
        self.assertIn("Windows Credential Manager", ui)
        self.assertIn("on_click=_auto_enable", ui)
        self.assertIn("on_click=_auto_disable", ui)
        self.assertNotIn("DHAN_TOTP_SECRET =", ui)
        self.assertNotIn("from src.broker", auth)
        self.assertNotIn("import resultbot", auth.lower())
        self.assertNotIn("/orders", auth)


if __name__ == "__main__":
    unittest.main()
