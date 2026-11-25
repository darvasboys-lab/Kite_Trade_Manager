"""Offline Dhan profile readiness tests; no real Dhan or order requests."""
import unittest
from unittest.mock import Mock

import requests

from dhan_visual.dhan_readonly_check import (
    PROFILE_URL, DhanProfileError, check_dhan_data_plan,
)


CLIENT = "1234567890"
TOKEN = "LOCAL_FAKE_LONG_TOKEN_FOR_TEST_ONLY"


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload if payload is not None else {
            "dhanClientId": CLIENT,
            "dataPlan": "Active",
            "dataValidity": "2026-12-05 09:37:52.0",
            "tokenValidity": "25/11/2026 20:00",
            "activeSegment": "Equity, Derivative",
        }

    def json(self):
        return self.payload


class DhanReadOnlyProfileTests(unittest.TestCase):
    def test_active_plan_and_valid_token_profile(self):
        session = Mock()
        session.get.return_value = FakeResponse()
        result = check_dhan_data_plan(CLIENT, TOKEN, session=session)
        self.assertTrue(result.data_ready)
        self.assertTrue(result.derivatives_enabled)
        self.assertIn("2026-12-05", result.data_validity)
        self.assertEqual(result.token_validity, "25/11/2026 20:00")
        session.get.assert_called_once_with(
            PROFILE_URL,
            headers={"access-token": TOKEN, "Accept": "application/json"},
            timeout=(5, 12), allow_redirects=False,
        )
        self.assertEqual(session.post.call_count, 0)

    def test_inactive_plan_is_not_mislabelled_ready(self):
        session = Mock()
        session.get.return_value = FakeResponse(payload={
            "dhanClientId": CLIENT, "dataPlan": "Deactive",
            "activeSegment": "Equity",
        })
        result = check_dhan_data_plan(CLIENT, TOKEN, session=session)
        self.assertFalse(result.data_ready)
        self.assertFalse(result.derivatives_enabled)

    def test_unverified_plan_cannot_appear_active(self):
        session = Mock()
        session.get.return_value = FakeResponse(payload={"dhanClientId": CLIENT})
        result = check_dhan_data_plan(CLIENT, TOKEN, session=session)
        self.assertEqual(result.data_plan, "Unknown")
        self.assertFalse(result.data_ready)
        self.assertIsNone(result.derivatives_enabled)

    def test_mismatched_account_rejected(self):
        session = Mock()
        session.get.return_value = FakeResponse(payload={
            "dhanClientId": "9876543210", "dataPlan": "Active",
        })
        with self.assertRaisesRegex(DhanProfileError, "does not match"):
            check_dhan_data_plan(CLIENT, TOKEN, session=session)

    def test_bad_auth_shows_status_without_exposing_token(self):
        for code in (401, 403):
            session = Mock()
            session.get.return_value = FakeResponse(status=code)
            with self.subTest(code=code), self.assertRaises(DhanProfileError) as caught:
                check_dhan_data_plan(CLIENT, TOKEN, session=session)
            self.assertIn(str(code), str(caught.exception))
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_no_network_on_invalid_credentials(self):
        session = Mock()
        with self.assertRaises(DhanProfileError):
            check_dhan_data_plan("invalid", TOKEN, session=session)
        with self.assertRaises(DhanProfileError):
            check_dhan_data_plan(CLIENT, "short", session=session)
        session.get.assert_not_called()

    def test_network_failure_does_not_leak_exception_request(self):
        session = Mock()
        session.get.side_effect = requests.Timeout("Bearer " + TOKEN)
        with self.assertRaises(DhanProfileError) as caught:
            check_dhan_data_plan(CLIENT, TOKEN, session=session)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("Timeout", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
