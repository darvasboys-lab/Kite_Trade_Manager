"""Offline one-time Dhan integration milestone and secure-vault behavior tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from dhan_visual.dhan_integration_state import (
    IST, parse_dhan_token_expiry, verified_account, record_verification,
    refresh_verified_token, token_still_current, save_manual_token,
    load_manual_token, clear_manual_token, VAULT_SERVICE,
)
from dhan_visual.auto_auth import AutoAuthError


CID = "1234567890"
TOKEN = "FAKE_DHAN_TOKEN_LONG_FOR_TESTS_ONLY"


class FakeVault:
    def __init__(self):
        self.items = {}
    def set_password(self, service, account, secret):
        self.items[(service, account)] = secret
    def get_password(self, service, account):
        return self.items.get((service, account))
    def delete_password(self, service, account):
        self.items.pop((service, account), None)


class DhanOnboardingStateTests(unittest.TestCase):
    def test_profile_token_validity_formats(self):
        expected = datetime(2026, 11, 25, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(parse_dhan_token_expiry("25/11/2026 20:00"), expected)
        self.assertEqual(parse_dhan_token_expiry("2026-11-25T20:00:00"), expected)
        self.assertIsNone(parse_dhan_token_expiry("wrong date"))

    def test_onboarding_record_survives_restart_without_any_secrets(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            expiry = (datetime.now(IST) + timedelta(hours=14)).strftime(
                "%d/%m/%Y %H:%M"
            )
            state = record_verification(
                CID, "TCS", "2026-12-29", expiry, path=path
            )
            loaded = verified_account(CID, path)
            self.assertEqual(loaded, state)
            self.assertTrue(token_still_current(loaded))
            self.assertIsNone(verified_account("5555555555", path))
            raw = path.read_text(encoding="utf8")
            self.assertNotIn(TOKEN, raw)
            for secret_name in ("access_token", "pin", "totp_secret"):
                self.assertNotIn(secret_name, raw)
            self.assertEqual(json.loads(raw)["client_id"], CID)
            self.assertEqual(loaded.symbol, "TCS")

    def test_expired_token_cannot_be_reported_as_active_after_completed_setup(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            expiry = (datetime.now(IST) - timedelta(days=1)).strftime(
                "%d/%m/%Y %H:%M"
            )
            record_verification(CID, "SBIN", "2026-12-29", expiry, path=path)
            saved = verified_account(CID, path)
            self.assertIsNotNone(saved)
            self.assertFalse(token_still_current(saved))

    def test_token_renewal_does_not_repeat_original_one_stock_integration(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            original = record_verification(
                CID, "TCS", "2026-12-29", "25/11/2026 20:00", path=path,
            )
            expiry = (datetime.now(IST) + timedelta(hours=18)).strftime(
                "%d/%m/%Y %H:%M"
            )
            updated = refresh_verified_token(CID, expiry, path=path)
            self.assertEqual(updated.verified_at, original.verified_at)
            self.assertEqual(updated.symbol, "TCS")
            self.assertEqual(updated.expiry, "2026-12-29")
            self.assertTrue(token_still_current(updated))

    def test_windows_vault_only_after_explicit_function_call(self):
        fake = FakeVault()
        self.assertEqual(fake.items, {})
        expiry = (datetime.now(IST) + timedelta(hours=14)).strftime(
            "%d/%m/%Y %H:%M"
        )
        with patch("dhan_visual.dhan_integration_state._keyring", return_value=fake):
            save_manual_token(CID, TOKEN, expiry)
            self.assertIn((VAULT_SERVICE, CID), fake.items)
            self.assertEqual(load_manual_token(CID), TOKEN)
            clear_manual_token(CID)
            self.assertEqual(load_manual_token(CID), "")

    def test_expired_manual_tokens_are_never_reused(self):
        fake = FakeVault()
        fake.set_password(VAULT_SERVICE, CID, json.dumps({
            "version": 1, "token": TOKEN,
            "expires": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        }))
        with patch("dhan_visual.dhan_integration_state._keyring", return_value=fake):
            self.assertEqual(load_manual_token(CID), "")
            with self.assertRaisesRegex(AutoAuthError, "expiry"):
                save_manual_token(CID, TOKEN, "01/01/2000 20:00")

    def test_symlink_or_malformed_record_fails_closed(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            path.write_text('{"access_token":"unsafe"}', encoding="utf8")
            self.assertIsNone(verified_account(CID, path))
            link = Path(folder) / "linked.json"
            link.symlink_to(path)
            self.assertIsNone(verified_account(CID, link))
            with self.assertRaises(OSError):
                record_verification(
                    CID, "TCS", "2026-12-29", "25/11/2026 20:00",
                    path=link,
                )


if __name__ == "__main__":
    unittest.main()
