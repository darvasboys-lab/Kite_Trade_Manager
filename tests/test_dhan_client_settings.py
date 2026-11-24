"""Ensure one-time Dhan Client ID persistence never writes an access token."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dhan_visual.local_client_settings import (
    read_client_id, save_client_id, valid_client_id
)


class ClientIdSettingsTests(unittest.TestCase):
    def test_remembers_id_across_calls_and_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".dhan_cache" / "client_settings.json"
            self.assertEqual(read_client_id(path), "")
            self.assertTrue(save_client_id("1234567890", path))
            self.assertEqual(read_client_id(path), "1234567890")
            self.assertTrue(save_client_id("1234567890", path))  # idempotent
            self.assertTrue(save_client_id("9876543210", path))
            self.assertEqual(read_client_id(path), "9876543210")
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc, {"version": 1, "client_id": "9876543210"})
            self.assertNotIn("token", path.read_text(encoding="utf-8").lower())

    def test_reject_jwt_secret_and_bad_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client_settings.json"
            for secret in (
                "abc.def.ghi", "eyJhbGciOiJIUzUxMiJ9.some.secret",
                "1234", "A1B2C3", "1234567890\nACCESS_TOKEN=wrong",
                "12345678901234567890",
            ):
                self.assertFalse(valid_client_id(secret))
                self.assertFalse(save_client_id(secret, path))
                self.assertFalse(path.exists())

    def test_malformed_or_oversized_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client_settings.json"
            for bad in (
                "{garbled", '{"version": 2, "client_id": "1234567890"}',
                '{"version": 1, "client_id": "eyJ.fake.token"}',
                '{"version": 1, "client_id": "1234567890", "token": "x"}' + " " * 513,
            ):
                path.write_text(bad, encoding="utf-8")
                self.assertEqual(read_client_id(path), "")

    def test_no_repository_or_broker_changes(self):
        root = Path(__file__).resolve().parents[1]
        ignore = (root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".dhan_cache/", ignore)
        source = (root / "dhan_visual" / "local_client_settings.py").read_text(encoding="utf-8")
        self.assertNotIn("requests.", source)
        self.assertNotIn("access-token", source)
        self.assertNotIn("DHAN_ACCESS_TOKEN", source)

    def test_streamlit_uses_saved_id_without_dhan_job_or_token(self):
        from streamlit.testing.v1 import AppTest
        from dhan_visual.dhan_market import DhanUnderlying
        from dhan_visual.screener import Underlying

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35)
        app.session_state["fno_universe_data"] = (
            [DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26": 225}), 11536)],
            "Verified fixture",
        )
        with patch("dhan_visual.screener_ui.read_client_id", return_value="1234567890"), \
             patch("dhan_visual.screener_ui.save_client_id") as saver, \
             patch.dict("os.environ", {"DHAN_ACCESS_TOKEN": ""}, clear=False):
            app.run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        self.assertEqual(app.text_input(key="dhan_local_cid").value, "1234567890")
        self.assertEqual(app.text_input(key="dhan_local_access_token").value, "")
        saver.assert_not_called()
        self.assertNotIn("fno_dhan_job", app.session_state)

    def test_streamlit_new_id_is_saved_but_token_is_not(self):
        from streamlit.testing.v1 import AppTest
        from dhan_visual.dhan_market import DhanUnderlying
        from dhan_visual.screener import Underlying

        app_file = Path(__file__).resolve().parents[1] / "dhan_visual" / "app.py"
        app = AppTest.from_file(str(app_file), default_timeout=35)
        app.session_state["fno_universe_data"] = (
            [DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26": 225}), 11536)],
            "Verified fixture",
        )
        app.session_state["dhan_local_cid"] = "1234567890"
        with patch("dhan_visual.screener_ui.read_client_id", return_value=""), \
             patch("dhan_visual.screener_ui.save_client_id", return_value=True) as save, \
             patch.dict("os.environ", {"DHAN_ACCESS_TOKEN": ""}, clear=False):
            app.run()
        self.assertFalse(app.exception, [str(x.message) for x in app.exception])
        save.assert_called_once_with("1234567890")


if __name__ == "__main__":
    unittest.main()
