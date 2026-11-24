"""Offline contract-screen and Dhan market-data unit tests. NO real broker calls."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from dhan_visual.dhan_market import (
    DhanDataError, DhanUnderlying, fetch_dhan_ltp, parse_dhan_ltp_response,
    parse_dhan_master, _expiry_month,
)
from dhan_visual.screener import Underlying, screen_rows, expiry_options

MASTER = """EXCH_ID,SEGMENT,SECURITY_ID,INSTRUMENT,INSTRUMENT_TYPE,TRADING_SYMBOL,SYMBOL_NAME,SERIES,UNDERLYING_SECURITY_ID,UNDERLYING_SYMBOL,LOT_SIZE,SM_EXPIRY_DATE
NSE,E,1333,EQUITY,EQ,SBIN,SBIN,EQ,,,1,
NSE,E,2885,EQUITY,EQ,RELIANCE,RELIANCE,EQ,,,1,
NSE,E,11536,EQUITY,EQ,TCS,TCS,EQ,,,1,
NSE,D,42001,OPTSTK,STKOPT,SBIN26NOVCE,SBIN,,-1,SBIN,750,2026-11-26
NSE,D,42002,OPTSTK,STKOPT,SBIN26DECCE,SBIN,,1333,SBIN,850,2026-12-31
NSE,D,42003,OPTSTK,STKOPT,SBIN26NOVPE,SBIN,,1333,SBIN,750,2026-11-26
NSE,D,51001,FUTSTK,STKFUT,TCS26NOV,TCS,,11536,TCS,225,2026-11-26
NSE,D,51002,OPTIDX,IDXOPT,NIFTY,NIFTY,,999,NIFTY,65,2026-11-26
NSE,D,51003,OPTSTK,STKOPT,RELIANCE,RELIANCE,,2885,RELIANCE,500,2026-11-26
"""
# In the base fixture the first SBIN record has an intentionally inconsistent ID:
# parser must ignore that record; the second is the verified current expiry.
FIXED = MASTER.replace("SBIN,,-1,SBIN,750", "SBIN,,1333,SBIN,750")


class MasterParsing(unittest.TestCase):
    def test_verified_only_stock_fno(self):
        records = parse_dhan_master(FIXED)
        self.assertEqual([x.entry.symbol for x in records], ["RELIANCE", "SBIN", "TCS"])
        self.assertEqual(next(x for x in records if x.entry.symbol == "SBIN").equity_security_id, 1333)
        self.assertEqual(next(x for x in records if x.entry.symbol == "SBIN").entry.lots, {"NOV-26": 750, "DEC-26": 850})
        self.assertNotIn("NIFTY", [x.entry.symbol for x in records])

    def test_index_exclusion_and_security_id_validation(self):
        # Isolate the invalid -1 record by removing the separate valid November put.
        invalid_only = MASTER.replace(
            "NSE,D,42003,OPTSTK,STKOPT,SBIN26NOVPE,SBIN,,1333,SBIN,750,2026-11-26\n", ""
        )
        records = parse_dhan_master(invalid_only)
        sbin = next(x for x in records if x.entry.symbol == "SBIN")
        self.assertEqual(sbin.entry.lots, {"DEC-26": 850})

    def test_conflicting_lot_is_excluded_for_month(self):
        conflict = FIXED + "NSE,D,42004,OPTSTK,STKOPT,SBIN26NOVCE2,SBIN,,1333,SBIN,999,2026-11-26\n"
        records = parse_dhan_master(conflict)
        sbin = next(x for x in records if x.entry.symbol == "SBIN")
        self.assertEqual(sbin.entry.lots, {"DEC-26": 850})

    def test_bad_csv_fail_closed(self):
        for bad in (b"\x00", "EXCH_ID,SEGMENT\nNSE,E", "wrong,header\nabc,def"):
            with self.assertRaises(DhanDataError):
                parse_dhan_master(bad)

    def test_expiry_parsing(self):
        self.assertEqual(_expiry_month("2026-11-26"), "NOV-26")
        self.assertEqual(_expiry_month("26-12-2026"), "DEC-26")
        self.assertEqual(_expiry_month("2027-01-28 14:30:00"), "JAN-27")
        self.assertIsNone(_expiry_month("nonsense"))

    def test_filter_works_with_dhan_quote(self):
        records = parse_dhan_master(FIXED)
        quotes = parse_dhan_ltp_response(
            {"status": "success", "data": {"NSE_EQ": {"1333": {"last_price": 650.0},
                                                     "2885": {"last_price": 1100}}}},
            {1333: "SBIN", 2885: "RELIANCE"}, "2026-11-24 UTC")
        result = screen_rows([r.entry for r in records], "NOV-26", quotes, max_value=500000)
        self.assertEqual([r["Symbol"] for r in result], ["SBIN"])
        self.assertEqual(result[0]["Contract value (₹)"], 487500.0)


class MarketDataParsing(unittest.TestCase):
    def test_valid_ltp_and_missing(self):
        snapshot = parse_dhan_ltp_response(
            {"status": "success", "data": {"NSE_EQ": {"1333": {"last_price": 650.5},
                                                     "11536": {"last_price": 0}}}},
            {1333: "SBIN", 11536: "TCS"}, "2026-11-24 UTC")
        self.assertEqual(snapshot["SBIN"].price, Decimal("650.50"))
        self.assertNotIn("TCS", snapshot)

    def test_reject_failure_and_wrong_segment(self):
        for response in ({"status": "failure"}, {"status": "success", "data": {"NSE_FNO": {}}}):
            with self.assertRaises(DhanDataError):
                parse_dhan_ltp_response(response, {1333: "SBIN"}, "time")

    def test_invalid_prices_rejected(self):
        response = {"status": "success", "data": {"NSE_EQ": {
            "1": {"last_price": "NaN"}, "2": {"last_price": -20},
            "3": {"last_price": "nan"}, "4": {"last_price": 123.45}}}}
        quotes = parse_dhan_ltp_response(response, {1:"A",2:"B",3:"C",4:"D"}, "time")
        self.assertEqual(list(quotes), ["D"])

    @patch("dhan_visual.dhan_market.QUOTE_LIMITER.acquire")
    @patch("requests.post")
    def test_batch_only_nse_eq_and_no_orders(self, post, limiter):
        response = unittest.mock.MagicMock()
        response.status_code = 200
        response.json.return_value = {"status": "success", "data": {"NSE_EQ": {
            "1333": {"last_price": 650}, "11536": {"last_price": 3010}}}}
        post.return_value = response
        records = [
            DhanUnderlying(Underlying("SBIN", "SBIN", {"NOV-26":750}),1333),
            DhanUnderlying(Underlying("TCS", "TCS", {"NOV-26":225}),11536),
        ]
        quotes,errors = fetch_dhan_ltp(records, "1234567890", "fake_token_for_unit_test_only", batch_size=2)
        self.assertFalse(errors)
        self.assertEqual(len(quotes),2)
        self.assertEqual(post.call_args.args[0], "https://api.dhan.co/v2/marketfeed/ltp")
        self.assertEqual(post.call_args.kwargs["json"], {"NSE_EQ": [1333,11536]})
        self.assertIn("access-token", post.call_args.kwargs["headers"])
        limiter.assert_called_once()

    @patch("dhan_visual.dhan_market.QUOTE_LIMITER.acquire")
    @patch("requests.post")
    def test_401_rejected_without_fallback(self, post, limiter):
        response = unittest.mock.MagicMock()
        response.status_code = 401
        post.return_value = response
        data = [DhanUnderlying(Underlying("SBIN","SBIN",{"NOV-26":750}),1333)]
        quotes,errors = fetch_dhan_ltp(data, "1234567890", "fake_token_for_unit_test_only")
        self.assertFalse(quotes)
        self.assertIn("401", errors[0])

    def test_credential_validation(self):
        with self.assertRaises(DhanDataError):
            fetch_dhan_ltp([], "1", "short")

if __name__ == "__main__":
    unittest.main()
