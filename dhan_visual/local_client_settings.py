"""Remember a Dhan Client ID on this PC; never persist access tokens.

Dhan Client ID is a stable account identifier, not an authentication secret.
Stored only in a private, gitignored local JSON file. The access token stays
in Streamlit's in-memory password input or explicit environment variable.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

CLIENT_SETTINGS_FILE = (
    Path(__file__).resolve().parent.parent
    / ".dhan_cache" / "client_settings.json"
)
_SCHEMA = 1
_ID_PATTERN = re.compile(r"^[0-9]{8,15}$")


def valid_client_id(candidate: str) -> bool:
    """Only save an ordinary numeric Dhan Client ID, never JWT/token material."""
    return isinstance(candidate, str) and bool(_ID_PATTERN.fullmatch(candidate.strip()))


def read_client_id(path: Path = CLIENT_SETTINGS_FILE) -> str:
    """Read a single stable ID; ignore malformed, oversized or unsafe files."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512:
            return ""
        obj = json.loads(path.read_text(encoding="utf-8"))
        cid = obj.get("client_id", "") if isinstance(obj, dict) and obj.get("version") == _SCHEMA else ""
        if valid_client_id(cid):
            return cid.strip()
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        pass
    return ""


def save_client_id(client_id: str, path: Path = CLIENT_SETTINGS_FILE) -> bool:
    """Atomically save the ID only; return False on invalid ID or disk error.

    The persisted JSON is deliberately generated from this whitelisted value
    rather than serializing Streamlit session state (which contains tokens).
    """
    if not valid_client_id(client_id):
        return False
    cid = client_id.strip()
    if path.is_symlink():
        return False
    if read_client_id(path) == cid:
        return True
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".client-",
            suffix=".tmp", dir=path.parent, delete=False
        ) as fp:
            temp_name = fp.name
            json.dump({"version": _SCHEMA, "client_id": cid}, fp)
            fp.write("\n")
        os.replace(temp_name, path)
        return True
    except OSError:
        return False
    finally:
        if temp_name is not None:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
