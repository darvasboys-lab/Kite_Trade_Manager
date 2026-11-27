import unittest
from urllib.parse import parse_qs, urlsplit

from dhan_visual.fyers_ui import prepare_saved_login
from dhan_visual.fyers_readonly import authorization_url, FyersDataError


class SavedLoginTests(unittest.TestCase):
    def test_saved_login_prepares_fresh_state_and_no_secret_in_url(self):
        state = {}
        credentials = ("TEST1234-100", "synthetic-private-secret")
        prepare_saved_login(state, credentials)
        previous = state["_fyers_oauth_state"]
        url = authorization_url(state["_fyers_pending_app"], previous)
        self.assertNotIn(credentials[1], url)
        self.assertEqual(parse_qs(urlsplit(url).query)["state"], [previous])
        prepare_saved_login(state, credentials)
        self.assertNotEqual(previous, state["_fyers_oauth_state"])

    def test_invalid_credentials_do_not_mutate_state(self):
        state = {}
        with self.assertRaises(FyersDataError):
            prepare_saved_login(state, ("invalid", "synthetic-secret"))
        self.assertEqual(state, {})
