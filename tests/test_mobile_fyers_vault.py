import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dhan_visual.fyers_readonly import FyersAccess
from dhan_visual.mobile_fyers_vault import MobileVault, VaultError


@unittest.skipUnless(os.name == "posix", "Mobile vault is Linux-only")
class MobileFyersVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "fyers.vault"
        self.password = "test strong passphrase"
        self.app = "TEST1234-100"
        self.secret = "synthetic-secret-only"

    def test_create_reopen_token_and_wrong_password(self):
        v = MobileVault.create(self.password, self.app, self.secret, self.path)
        a = FyersAccess(self.app, "synthetic-token", datetime.now(timezone.utc)+timedelta(hours=1))
        v.save_access(a, self.secret)
        raw = self.path.read_bytes()
        for value in (self.app, self.secret, a.token, self.password):
            self.assertNotIn(value.encode(), raw)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        restored = MobileVault.unlock(self.password, self.path)
        self.assertEqual(restored.credentials(), (self.app, self.secret))
        self.assertEqual(restored.access(), a)
        with self.assertRaises(VaultError):
            MobileVault.unlock("wrong passphrase", self.path)
        self.assertEqual(self.path.read_bytes(), raw)
        with self.assertRaises(VaultError):
            MobileVault.create(self.password, self.app, self.secret, self.path)
        restored.forget()
        self.assertFalse(self.path.exists())

    def test_reject_stale_writer_wrong_credentials_and_expired_token(self):
        v = MobileVault.create(self.password, self.app, self.secret, self.path)
        stale = MobileVault.unlock(self.password, self.path)
        a = FyersAccess(self.app, "synthetic-token", datetime.now(timezone.utc)+timedelta(hours=1))
        with self.assertRaises(VaultError):
            v.save_access(a, "wrong-secret")
        with self.assertRaises(VaultError):
            v.save_access(FyersAccess(self.app, "expired", datetime.now(timezone.utc)-timedelta(seconds=1)), self.secret)
        v.save_access(a, self.secret)
        with self.assertRaises(VaultError):
            stale.forget()
        v.save_credentials(self.app, "replacement-secret")
        self.assertIsNone(v.access())

    def test_tamper_and_insecure_permissions_fail_closed(self):
        v = MobileVault.create(self.password, self.app, self.secret, self.path)
        raw = self.path.read_bytes()
        self.path.chmod(0o644)
        with self.assertRaises(VaultError):
            MobileVault.unlock(self.password, self.path)
        self.path.chmod(0o600)
        self.path.write_bytes(raw[:-12] + b"X" * 12)
        with self.assertRaises(VaultError):
            MobileVault.unlock(self.password, self.path)
