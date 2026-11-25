"""Offline acceptance tests for FYERS NON-TRADING OAuth and screener market data."""
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

from dhan_visual import fyers_readonly as fy


class FakeHTTP:
    def __init__(self, content, status=200):
        self.status_code = status
        self.content = content
    def json(self):
        return self.content


class FyersDataOnlyTests(unittest.TestCase):
    def setUp(self):
        self.access = fy.FyersAccess(
            "ABCD1234-100", "test-token-not-real",
            datetime.now(timezone.utc) + timedelta(hours=8),
        )

    def test_new_state_and_strict_redirect_validation(self):
        state = fy.new_state()
        self.assertTrue(len(state) >= 24)
        url = fy.authorization_url("ABCD1234-100", state)
        self.assertIn("generate-authcode", url)
        self.assertIn("response_type=code", url)
        returned = fy.REDIRECT_URI + "?state=" + state + "&auth_code=abcdefghi0123456"
        self.assertEqual(
            fy.parse_authorization_return(returned, state),
            "abcdefghi0123456",
        )
        for bad in (
            fy.REDIRECT_URI + "?state=WRONG&auth_code=abcdefghi0123456",
            "https://example.com/redirect?state=" + state + "&auth_code=abcdefghi0123456",
            fy.REDIRECT_URI + "?state=" + state + "&auth_code=abcdefghi0123456&auth_code=abcdefghi0123456",
        ):
            with self.subTest(bad=bad[:45]), self.assertRaises(fy.FyersDataError):
                fy.parse_authorization_return(bad, state)

    def test_app_credentials_exchange_only_with_fyers_oauth_endpoint(self):
        response = Mock(return_value=FakeHTTP({
            "s": "ok", "code": 200, "access_token": "token-from-fyers",
        }))
        out = fy.exchange_code(
            "ABCD1234-100", "private-secret", "sample-authorization-code",
            post=response, now=datetime.now(timezone.utc),
        )
        self.assertEqual(out.token, "token-from-fyers")
        self.assertTrue(out.valid())
        args, kwargs = response.call_args
        self.assertEqual(args[0], fy.API_ROOT + "/api/v3/validate-authcode")
        self.assertNotIn("private-secret", str(kwargs))
        self.assertEqual(kwargs["json"]["grant_type"], "authorization_code")

    def test_no_fake_cash_values_or_cross_symbol_mapping(self):
        payload = {
            "s": "ok", "d": [
                {"n": "NSE:TCS-EQ", "s": "ok", "v": {"lp": 100.5}},
                {"n": "NSE:SBIN-EQ", "s": "ok", "v": {"lp": 600}},
                {"n": "NSE:INFY-EQ", "s": "error", "v": {"lp": 1500}},
                {"n": "NSE:WIPRO-EQ", "s": "ok", "v": {"lp": 0}},
            ],
        }
        result = fy.parse_quotes(payload, ["TCS", "INFY", "WIPRO"], "test")
        self.assertEqual(list(result), ["TCS"])
        self.assertEqual(result["TCS"].price, Decimal("100.5"))

    def test_real_http_mock_only_bounded_get_data_calls(self):
        mock = Mock(return_value=FakeHTTP({
            "s": "ok", "d": [
                {"n": "NSE:TCS-EQ", "s": "ok", "v": {"lp": 100.5}},
            ],
        }))
        with patch.object(fy, "_rate_gate"):
            quotes = fy.fetch_cash_quotes(self.access, ["TCS", "TCS", "INFY"], get=mock)
        self.assertEqual(quotes["TCS"].price, Decimal("100.5"))
        self.assertEqual(mock.call_count, 1)
        args, kwargs = mock.call_args
        self.assertEqual(args[0], fy.API_ROOT + "/data/quotes")
        self.assertEqual(kwargs["params"]["symbols"], "NSE:TCS-EQ,NSE:INFY-EQ")
        self.assertTrue(kwargs["headers"]["Authorization"].startswith(self.access.app_id + ":"))
        self.assertFalse(kwargs["allow_redirects"])

    def test_rate_limit_and_unauthorized_rejected_not_bypassed(self):
        for code in (401, 403, 429):
            with self.subTest(code=code), patch.object(fy, "_rate_gate"):
                get = Mock(return_value=FakeHTTP({}, status=code))
                with self.assertRaises(fy.FyersDataError):
                    fy.fetch_cash_quotes(self.access, ["TCS"], get=get)
                self.assertEqual(get.call_count, 1)

    def sample_chain(self):
        return {"s": "ok", "data": {"optionsChain": [
            {"option_type": "", "strike_price": -1, "ltp": 100, "symbol": "NSE:TCS-EQ"},
            {"option_type": "CE", "strike_price": 100, "ltp": 5, "symbol": "NSE:TCS26DEC100CE", "bid": 4.5},
            {"option_type": "PE", "strike_price": 100, "ltp": 7, "symbol": "NSE:TCS26DEC100PE", "bid": 6.5},
            {"option_type": "CE", "strike_price": 105, "ltp": 2, "symbol": "NSE:TCS26DEC105CE", "volume": 20},
            {"option_type": "CE", "strike_price": 110, "ltp": 1, "symbol": "NSE:TCS26DEC110CE"},
        ]}}

    def test_genuine_first_strike_at_least_five_percent_and_atm(self):
        call, straddle = fy.parse_chain(self.sample_chain(), "TCS", "DEC-26")
        self.assertEqual(call.strike, Decimal("105"))
        self.assertEqual(call.premium_pct, Decimal("2.00"))
        self.assertEqual(call.underlying_ltp, Decimal("100"))
        self.assertEqual(straddle.strike, Decimal("100"))
        self.assertEqual(straddle.premium_pct, Decimal("12.00"))

    def test_missing_real_leg_is_blank_not_synthetic(self):
        payload = self.sample_chain()
        payload["data"]["optionsChain"] = [
            row for row in payload["data"]["optionsChain"]
            if row.get("option_type") != "PE"
        ]
        call, straddle = fy.parse_chain(payload, "TCS", "DEC-26")
        self.assertIsNotNone(call)
        self.assertIsNone(straddle)
        missing = self.sample_chain()
        missing["data"]["optionsChain"][0]["symbol"] = "NSE:SBIN-EQ"
        self.assertEqual(fy.parse_chain(missing, "TCS", "DEC-26"), (None, None))

    def test_explicit_monthly_expiry_not_wrong_default_expiry(self):
        data = {"s": "ok", "data": {"expiryData": [
            {"date": "03-12-2026", "expiry": "111", "expiry_flag": "W"},
            {"date": "24-12-2026", "expiry": "222", "expiry_flag": "M"},
            {"date": "31-12-2026", "expiry": "333", "expiry_flag": "W"},
            {"date": "28-01-2027", "expiry": "444", "expiry_flag": "M"},
        ]}}
        get = Mock(return_value=FakeHTTP(data))
        with patch.object(fy, "_rate_gate"):
            stamp, iso = fy.resolve_expiry_timestamp(self.access, "DEC-26", get=get)
        self.assertEqual((stamp, iso), ("222", "2026-12-24"))
        self.assertEqual(get.call_args.kwargs["params"]["symbol"], "NSE:SBIN-EQ")
        self.assertEqual(fy._select_expiry(data["data"]["expiryData"], "FEB-27"), None)

    def test_fyers_daily_data_produces_real_ema_and_monthly_r1(self):
        day = date(2026, 11, 25)
        start = day - timedelta(days=360)
        bars = []
        for i in range(361):
            current = start + timedelta(days=i)
            if current.weekday() < 5:
                stamp = int(datetime.combine(current, datetime.min.time(),
                                            fy.INDIA).timestamp())
                close = 100 + i * 0.1
                bars.append([stamp, close, close + 3, close - 2, close + 1, 1000])
        get = Mock(return_value=FakeHTTP({"s": "ok", "candles": bars}))
        with patch.object(fy, "_rate_gate"):
            levels = fy.fetch_daily_levels(self.access, "TCS", day=day, get=get)
        self.assertIsNotNone(levels.ema200)
        self.assertIsNotNone(levels.ema50)
        self.assertIsNotNone(levels.monthly_r1)
        self.assertEqual(levels.asof, "2026-11-24")
        self.assertEqual(get.call_args.kwargs["params"]["resolution"], "D")

    def test_fyers_r1_todays_completed_15m_cross_is_verified(self):
        from dhan_visual.r1_today import session_r1_cross
        day = date(2026, 11, 25)
        now = datetime(2026, 11, 25, 10, 2, tzinfo=fy.INDIA)
        def bar(y, m, d, hh, mm, o, h, lo, close):
            return [
                int(datetime(y, m, d, hh, mm, tzinfo=fy.INDIA).timestamp()),
                o, h, lo, close, 100,
            ]
        payload = {"s": "ok", "candles": [
            bar(2026, 11, 23, 15, 15, 95, 97, 94, 95),
            bar(2026, 11, 25, 9, 15, 99, 101, 98, 100.5),
            bar(2026, 11, 25, 9, 30, 100.5, 102, 100, 101),
            bar(2026, 11, 25, 9, 45, 101, 105, 101, 104),
        ]}
        get = Mock(return_value=FakeHTTP(payload))
        with patch.object(fy, "_rate_gate"):
            candles = fy.fetch_r1_intraday_bars(
                self.access, "TCS", day=day, now=now, get=get,
            )
        # At 10:02 the 9:45 bar has not yet finished; skip it.
        self.assertEqual(len(candles), 3)
        crossing, close = session_r1_cross(
            candles, Decimal("100"), day,
        )
        self.assertEqual(crossing.first_bar_time, "09:15")
        self.assertEqual(close, Decimal("101.0"))

    def test_token_expiry_respects_0630_ist(self):
        early = datetime(2026, 11, 25, 6, 29, tzinfo=fy.INDIA)
        late = datetime(2026, 11, 25, 6, 31, tzinfo=fy.INDIA)
        self.assertEqual(
            fy.next_token_expiry(early).astimezone(fy.INDIA).date(),
            date(2026, 11, 25),
        )
        self.assertEqual(
            fy.next_token_expiry(late).astimezone(fy.INDIA).date(),
            date(2026, 11, 26),
        )


if __name__ == "__main__":
    unittest.main()
