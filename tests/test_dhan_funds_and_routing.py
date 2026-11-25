"""No-network tests for Dhan available funds and conservative quote routing."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock
import unittest

import requests

from dhan_visual.dhan_funds import (
    FUNDS_URL, DhanFundsError, read_dhan_balance,
)
from dhan_visual.quote_routing import (
    AUTO_SOURCE, choose_cash_source, merge_dhan_yahoo,
    missing_underlyings, retry_window_seconds, should_fetch_again,
    nse_regular_session, IST,
)

CID = "1234567890"
TOKEN = "SYNTHETIC_DO_NOT_USE_AS_BROKER_TOKEN"


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else {
            "dhanClientId": CID,
            "availabelBalance": 98440.75,
            "utilizedAmount": 15202,
            "withdrawableBalance": 98310.50,
        }
    def json(self):
        return self.data


class FundsTests(unittest.TestCase):
    def test_official_read_only_get_and_available_balance_misspelling(self):
        session = Mock()
        session.get.return_value = FakeResponse()
        result = read_dhan_balance(CID, TOKEN, session=session)
        self.assertEqual(result.available, Decimal("98440.75"))
        self.assertEqual(result.utilized, Decimal("15202"))
        self.assertEqual(result.withdrawable, Decimal("98310.5"))
        session.get.assert_called_once_with(
            FUNDS_URL,
            headers={"access-token": TOKEN, "Accept": "application/json"},
            timeout=(5, 12), allow_redirects=False,
        )
        session.post.assert_not_called()

    def test_zero_balance_is_real_and_missing_balance_is_unavailable(self):
        s = Mock()
        s.get.return_value = FakeResponse(data={
            "dhanClientId": CID, "availabelBalance": 0,
        })
        self.assertEqual(read_dhan_balance(CID, TOKEN, session=s).available, 0)
        for missing in ("availableBalance", "availabelBalance"):
            data = {"dhanClientId": CID}
            if missing == "availableBalance":
                data[missing] = 125
            s.get.return_value = FakeResponse(data=data)
            with self.subTest(missing=missing), self.assertRaises(DhanFundsError):
                read_dhan_balance(CID, TOKEN, session=s)

    def test_wrong_account_or_bad_values_rejected_without_data_leak(self):
        s = Mock()
        for payload in (
            {"dhanClientId": "9876543210", "availabelBalance": 100},
            {"dhanClientId": CID, "availabelBalance": -1},
            {"dhanClientId": CID, "availabelBalance": float("nan")},
            {"dhanClientId": CID, "availabelBalance": True},
        ):
            s.get.return_value = FakeResponse(data=payload)
            with self.subTest(payload=payload), self.assertRaises(DhanFundsError):
                read_dhan_balance(CID, TOKEN, session=s)

    def test_rate_limit_and_auth_fail_without_retry(self):
        for code in (401, 403, 429):
            s = Mock()
            s.get.return_value = FakeResponse(status=code)
            with self.subTest(code=code), self.assertRaises(DhanFundsError) as err:
                read_dhan_balance(CID, TOKEN, session=s)
            self.assertNotIn(TOKEN, str(err.exception))
            s.get.assert_called_once()

    def test_request_timeout_cannot_leak_secret(self):
        s = Mock()
        s.get.side_effect = requests.Timeout("Bearer " + TOKEN)
        with self.assertRaises(DhanFundsError) as err:
            read_dhan_balance(CID, TOKEN, session=s)
        self.assertNotIn(TOKEN, str(err.exception))


class QuoteRoutingTests(unittest.TestCase):
    def test_auto_uses_verified_dhan_only_with_token(self):
        self.assertEqual(
            choose_cash_source(AUTO_SOURCE, verified=True, token_available=True),
            "Dhan",
        )
        for verified, token in ((False, False), (True, False), (False, True)):
            self.assertEqual(
                choose_cash_source(
                    AUTO_SOURCE, verified=verified, token_available=token,
                ),
                "Yahoo Finance",
            )
        self.assertEqual(
            choose_cash_source("Yahoo Finance", verified=True, token_available=True),
            "Yahoo Finance",
        )
        self.assertEqual(
            choose_cash_source("Dhan", verified=False, token_available=False),
            "Dhan",
        )

    def test_dhan_wins_only_its_own_symbol_yahoo_fills_missing(self):
        from dhan_visual.screener import Quote, Underlying
        from dhan_visual.dhan_market import DhanUnderlying
        dhan = {"TCS": Quote(Decimal("100"), "now", "Dhan NSE_EQ LTP")}
        yahoo = {
            "TCS": Quote(Decimal("95"), "bar", "Yahoo"),
            "SBIN": Quote(Decimal("610"), "bar", "Yahoo"),
        }
        merged = merge_dhan_yahoo(dhan, yahoo)
        self.assertEqual(merged["TCS"], dhan["TCS"])
        self.assertEqual(merged["SBIN"], yahoo["SBIN"])
        universe = [
            DhanUnderlying(Underlying("TCS", "TCS", {"DEC-26": 225}), 11536),
            DhanUnderlying(Underlying("SBIN", "SBIN", {"DEC-26": 750}), 1333),
        ]
        self.assertEqual(
            [x.entry.symbol for x in missing_underlyings(universe, dhan)],
            ["SBIN"],
        )

    def test_regular_nse_hours_gate_and_off_hours_cooldown(self):
        self.assertTrue(nse_regular_session(
            datetime(2026, 11, 25, 9, 15, tzinfo=IST)
        ))
        self.assertTrue(nse_regular_session(
            datetime(2026, 11, 25, 15, 30, tzinfo=IST)
        ))
        self.assertFalse(nse_regular_session(
            datetime(2026, 11, 25, 15, 31, tzinfo=IST)
        ))
        self.assertFalse(nse_regular_session(
            datetime(2026, 11, 28, 10, 0, tzinfo=IST)
        ))
        self.assertFalse(should_fetch_again(
            100, now=1000, market_open=False
        ))
        self.assertTrue(should_fetch_again(
            100, now=3700, market_open=False
        ))

    def test_dhan_quote_cooldown_and_rate_limit_stop_retries(self):
        self.assertFalse(should_fetch_again(100, now=300))
        self.assertTrue(should_fetch_again(100, now=401))
        self.assertTrue(should_fetch_again(100, now=106, manual=True))
        self.assertFalse(should_fetch_again(100, now=101, manual=True))
        self.assertEqual(retry_window_seconds(["HTTP 429"]), 1800)
        self.assertFalse(should_fetch_again(
            100, now=450, errors=["HTTP 429"], manual=True
        ))
        self.assertTrue(should_fetch_again(
            100, now=1901, errors=["HTTP 429"]
        ))


if __name__ == "__main__":
    unittest.main()
