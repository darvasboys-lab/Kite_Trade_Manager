import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from dhan_visual import fyers_local_oauth as oauth
from dhan_visual.fyers_readonly import FyersDataError

CREDS = ("TEST1234-100", "secret-for-offline-test")
CODE = "fake-authorization-code"


class LocalOAuthTests(unittest.TestCase):
    def setUp(self):
        oauth._PENDING.clear()

    def tearDown(self):
        oauth._PENDING.clear()

    def test_link_and_one_use_state(self):
        state,url=oauth.prepare(CREDS)
        self.assertEqual(parse_qs(urlsplit(url).query)["redirect_uri"],[oauth.CALLBACK])
        self.assertNotIn(CREDS[1],url)
        self.assertEqual(oauth.prepare(CREDS,previous=state)[0],state)
        with patch.object(oauth,"exchange_code",return_value="token") as exchange, patch.object(oauth,"fetch_cash_quotes",return_value={"TCS":object()}) as quote:
            self.assertEqual(oauth.consume([state],[CODE],windows_credentials=CREDS),("token",None,CREDS[1]))
            exchange.assert_called_once_with(*CREDS,CODE)
            quote.assert_called_once_with("token",["TCS"])
            with self.assertRaises(FyersDataError):
                oauth.consume([state],[CODE],windows_credentials=CREDS)
            exchange.assert_called_once()

    def test_invalid_expired_changed_or_nonlocal_never_exchanges(self):
        with patch.object(oauth,"exchange_code") as exchange:
            state,_=oauth.prepare(CREDS)
            for states,codes,kwargs in [
                ([state,state],[CODE],{"windows_credentials":CREDS}),
                ([state],[CODE],{"windows_credentials":CREDS,"allow":False}),
                (["bad"],[CODE],{"windows_credentials":CREDS}),
                ([state],[CODE],{"windows_credentials":("TEST1234-100","changed-secret")}),
            ]:
                with self.assertRaises(FyersDataError):
                    oauth.consume(states,codes,**kwargs)
            with patch.object(oauth,"monotonic",return_value=10):
                state,_=oauth.prepare(CREDS)
            with patch.object(oauth,"monotonic",return_value=611):
                with self.assertRaises(FyersDataError):
                    oauth.consume([state],[CODE],windows_credentials=CREDS)
            exchange.assert_not_called()

    def test_mobile_vault_handoff_requires_verified_quote(self):
        class Vault:
            def credentials(self):
                return CREDS
        vault=Vault()
        state,_=oauth.prepare(CREDS,vault)
        with patch.object(oauth,"exchange_code",return_value="token"), patch.object(oauth,"fetch_cash_quotes",return_value={}):
            with self.assertRaises(FyersDataError):
                oauth.consume([state],[CODE])
        state,_=oauth.prepare(CREDS,vault)
        with patch.object(oauth,"exchange_code",return_value="token"), patch.object(oauth,"fetch_cash_quotes",return_value={"TCS":object()}):
            self.assertEqual(oauth.consume([state],[CODE]),("token",vault,CREDS[1]))

if __name__=="__main__":
    unittest.main()
