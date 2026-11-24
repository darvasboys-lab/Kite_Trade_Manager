"""Offline tests for on-demand Dhan ATM CE/PE and numeric-lakh display."""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from dhan_visual.atm_options import (
    AtmOptionQuote, OptionDataError, OptionLeg, _number, _month_number,
    nearest_active_expiry, parse_atm_option_chain, load_atm_options,
    EXPIRIES_URL, OPTION_CHAIN_URL, IST,
)
from dhan_visual.option_panel import format_rupees


def fake_chain():
    return {
        "status": "success",
        "data": {
            "last_price": 1018,
            "oc": {
                "1000.000000": {
                    "ce": {
                        "last_price": 36.75, "top_bid_price": 36.5,
                        "top_ask_price": 37.0,
                    },
                    "pe": {
                        "last_price": 22.50, "top_bid_price": 22.2,
                        "top_ask_price": 23.0,
                    }
                },
                "1050.000000": {
                    "ce": {"last_price": 25}, "pe": {"last_price": 37}
                },
                "99999": {"ce": {"last_price": 3}}
            }
        }
    }


class AtmOptionDataTests(unittest.TestCase):
    def test_select_nearest_strike_from_real_underlying_ltp(self):
        result = parse_atm_option_chain(fake_chain(), "TEST", "2026-11-26", 750, "test")
        self.assertEqual(result.strike, Decimal("1000.000000"))
        self.assertEqual(result.underlying_price, Decimal("1018"))
        self.assertEqual(result.ce.ltp, Decimal("36.75"))
        self.assertEqual(result.pe.bid, Decimal("22.2"))
        self.assertEqual(result.ce.premium_per_lot(750), Decimal("27562.50"))
        self.assertEqual(result.pe.premium_per_lot(750), Decimal("16875.00"))

    def test_nearest_strike_ties_choose_lower_and_missing_leg_is_unknown(self):
        fixture = {
            "status": "success",
            "data": {
                "last_price": 1025,
                "oc": {
                    "1000": {"ce": {"last_price": 10}},
                    "1050": {"pe": {"last_price": 15}}
                }
            }
        }
        result = parse_atm_option_chain(fixture, "TEST", "2026-12-31", 250, "x")
        self.assertEqual(result.strike, Decimal("1000"))
        self.assertIsNone(result.pe.ltp)
        self.assertIsNone(result.pe.premium_per_lot(250))

    def test_reject_invalid_prices_and_fabricated_atm(self):
        self.assertIsNone(_number("NaN"))
        self.assertIsNone(_number("-3"))
        self.assertIsNone(_number("0"))
        for broken in [
            {"status": "error"},
            {"status": "success", "data": {"last_price": 0, "oc": {"1000": {"ce": {}}}}},
            {"status": "success", "data": {"last_price": 500, "oc": {}}},
        ]:
            with self.assertRaises(OptionDataError):
                parse_atm_option_chain(broken, "TEST", "2026-12-31", 250, "x")

    def test_select_active_expiry_within_correct_month(self):
        today = date(2026, 11, 24)
        payload = {
            "status": "success",
            "data": ["2026-11-19", "2026-11-26", "2026-12-31",
                     "2026-11-30", "malformed"]
        }
        self.assertEqual(
            nearest_active_expiry(payload, "NOV-26", today=today), "2026-11-26"
        )
        with self.assertRaisesRegex(OptionDataError, "No active"):
            nearest_active_expiry(payload, "OCT-26", today=today)

    @patch("dhan_visual.atm_options.RATE_LIMITER.acquire")
    def test_one_selected_stock_only_with_two_readonly_posts(self, throttle):
        session = MagicMock()
        future = datetime.now(IST) + timedelta(days=15)
        expiry = future.date().isoformat()
        month = future.strftime("%b-%y").upper()
        first, second = MagicMock(), MagicMock()
        first.status_code = 200
        first.json.return_value = {
            "status": "success", "data": [expiry]
        }
        second.status_code = 200
        second.json.return_value = fake_chain()
        session.post.side_effect = [first, second]
        result = load_atm_options(
            "TEST", month, 750, 12345, "1234567890",
            "UNIT_TEST_TOKEN_WITHOUT_AUTH", session=session
        )
        self.assertEqual(result.expiry, expiry)
        self.assertEqual(result.ce.ltp, Decimal("36.75"))
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(session.post.call_args_list[0].args[0], EXPIRIES_URL)
        self.assertEqual(session.post.call_args_list[1].args[0], OPTION_CHAIN_URL)
        self.assertEqual(
            session.post.call_args_list[0].kwargs["json"],
            {"UnderlyingScrip": 12345, "UnderlyingSeg": "NSE_EQ"}
        )
        self.assertEqual(
            session.post.call_args_list[1].kwargs["json"],
            {"UnderlyingScrip": 12345, "UnderlyingSeg": "NSE_EQ", "Expiry": expiry}
        )
        throttle.assert_called()
        self.assertEqual(throttle.call_count, 2)

    @patch("dhan_visual.atm_options.RATE_LIMITER.acquire")
    def test_auth_error_is_safe_and_no_trade_endpoint(self, throttle):
        session = MagicMock()
        session.post.return_value.status_code = 403
        with self.assertRaisesRegex(OptionDataError, "HTTP 403") as error:
            load_atm_options(
                "TEST", "NOV-26", 250, 100, "1234567890",
                "UNIT_TEST_TOKEN_WITHOUT_AUTH", session=session
            )
        self.assertNotIn("UNIT_TEST_TOKEN_WITHOUT_AUTH", str(error.exception))
        self.assertEqual(session.post.call_args.args[0], EXPIRIES_URL)
        self.assertNotIn("orders", session.post.call_args.args[0])

    def test_rupee_display_only_changes_presentation(self):
        self.assertEqual(format_rupees(270500, compact=True), "₹2.7L")
        self.assertEqual(format_rupees(270500, compact=False), "₹270,500.00")
        self.assertEqual(format_rupees(None, compact=True), "N/A")


if __name__ == "__main__":
    unittest.main()
