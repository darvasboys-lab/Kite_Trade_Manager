"""Opt-in passphrase-encrypted FYERS vault for local Android/Linux CCS.

No key or plaintext credentials on disk. The unlocked key/payload live only
in the individual Streamlit browser session. Not an Android Keystore bridge.
No broker requests. No Dhan PIN or TOTP storage.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .fyers_readonly import FyersAccess, validate_app_id, _valid_secret
from datetime import datetime

MAX_BYTES = 65536
ITERATIONS = 600_000
HEADER = b"CCSFY1:"


class VaultError(ValueError):
    """Redacted vault failure."""


def location():
    return Path.home() / ".ccs-private" / "fyers.vault"


def exists(path=None):
    return os.path.lexists(Path(path) if path is not None else location())


def _key(passphrase, salt):
    if not isinstance(passphrase, str) or not 10 <= len(passphrase) <= 256:
        raise VaultError("Use a vault passphrase of 10 to 256 characters.")
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, ITERATIONS, dklen=32))


def _secure_dir(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise VaultError("Vault directory is not private; refusing storage.")


@contextmanager
def _locked(path):
    # Local Linux/Termux only. Never follow a symlink for the lock or vault.
    import fcntl
    _secure_dir(path)
    fd = os.open(path.parent / ".fyers.lock",
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise VaultError("Vault lock is not private.")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or info.st_size > MAX_BYTES):
            raise VaultError("Vault file is not private or is invalid.")
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise VaultError("Vault file is too large.")
    return raw


def _write(path, data):
    if len(data) > MAX_BYTES:
        raise VaultError("Vault data is too large.")
    fd, temp = tempfile.mkstemp(prefix=".fyers-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _decode(raw, cipher):
    try:
        if not raw.startswith(HEADER) or len(raw) <= len(HEADER) + 16:
            raise ValueError()
        data = json.loads(cipher.decrypt(raw[len(HEADER) + 16:]))
        if data["v"] != 1:
            raise ValueError()
        data["id"] = validate_app_id(data["id"])
        data["secret"] = _valid_secret(data["secret"])
        return data
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise VaultError("Wrong passphrase or damaged FYERS vault.") from exc


class MobileVault:
    def __init__(self, path, salt, cipher, raw, data):
        self.path, self.salt, self.cipher = path, salt, cipher
        self.raw, self.data = raw, data

    @classmethod
    def create(cls, passphrase, app_id, secret, path=None):
        path = Path(path) if path is not None else location()
        app_id, secret = validate_app_id(app_id), _valid_secret(secret)
        salt = os.urandom(16)
        cipher = Fernet(_key(passphrase, salt))
        data = {"v": 1, "id": app_id, "secret": secret}
        raw = HEADER + salt + cipher.encrypt(json.dumps(data).encode())
        try:
            with _locked(path):
                if os.path.lexists(path):
                    raise VaultError("A vault already exists. Unlock it first.")
                _write(path, raw)
        except OSError as exc:
            raise VaultError("Could not save private FYERS vault.") from exc
        return cls(path, salt, cipher, raw, data)

    @classmethod
    def unlock(cls, passphrase, path=None):
        path = Path(path) if path is not None else location()
        try:
            with _locked(path):
                raw = _read(path)
            if not raw.startswith(HEADER) or len(raw) <= len(HEADER) + 16:
                raise VaultError("Invalid FYERS vault.")
            salt = raw[len(HEADER):len(HEADER) + 16]
            cipher = Fernet(_key(passphrase, salt))
            data = _decode(raw, cipher)
        except OSError as exc:
            raise VaultError("Could not read private FYERS vault.") from exc
        return cls(path, salt, cipher, raw, data)

    def credentials(self):
        return self.data["id"], self.data["secret"]

    def access(self):
        try:
            item = self.data["access"]
            access = FyersAccess(self.data["id"], item["token"],
                                 datetime.fromisoformat(item["expiry"]))
            return access if access.valid() else None
        except (KeyError, TypeError, ValueError):
            return None

    def _replace(self, data):
        raw = HEADER + self.salt + self.cipher.encrypt(json.dumps(data).encode())
        try:
            with _locked(self.path):
                if _read(self.path) != self.raw:
                    raise VaultError("Vault changed in another session. Reopen and unlock it.")
                _write(self.path, raw)
        except OSError as exc:
            raise VaultError("Could not update private FYERS vault.") from exc
        self.raw, self.data = raw, data

    def save_credentials(self, app_id, secret):
        app_id, secret = validate_app_id(app_id), _valid_secret(secret)
        if (app_id, secret) != self.credentials():
            self._replace({"v": 1, "id": app_id, "secret": secret})

    def save_access(self, access, app_secret):
        if (access.app_id, app_secret) != self.credentials() or not access.valid():
            raise VaultError("Token does not match saved credentials or has expired.")
        self._replace({**self.data, "access": {
            "token": access.token, "expiry": access.expires_at.isoformat()}})

    def forget(self):
        try:
            with _locked(self.path):
                if _read(self.path) != self.raw:
                    raise VaultError("Vault changed in another session. Reopen and unlock it.")
                self.path.unlink()
        except OSError as exc:
            raise VaultError("Could not delete FYERS vault.") from exc
