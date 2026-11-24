"""Offline tests for opt-in documented TejHQ Retail/Pro FO EOD chain."""
from datetime import date
from decimal import Decimal
from queue import SimpleQueue
from threading import Event
from unittest.mock import Mock
import json
import unittest

from dhan_visual.ce_tejhq import (
    TejEodError, TejEodCall, parse_tej_chain,
    read_tej_chain, scan_tej_eod, MAX_CHAIN_BYTES,
)


def valid_chain(*, symbol="TCS", asof="2026-11-23",
                expiry="2026-12-29", underlying=100):
    return {
        "meta": {"date": asof, "expiry": expiry, "underlying": underlying},
        "data": [
            {"instrument": "OPTSTK", "expiry": expiry, "strike": 104,
             "option_type": "CE", "close": 4.25, "contracts": 12},
            {"instrument": "OPTSTK", "expiry": expiry, "strike": 105,
             "option_type": "CE", "close": 2, "contracts": 0},
            {"instrument": "OPTSTK", "expiry": expiry, "strike": 110,
             "option_type": "CE", "close": 1, "contracts": 15},
            {"instrument": "OPTSTK", "expiry": expiry, "strike": 105,
             "option_type": "PE", "close": 5, "contracts": 22},
        ]
    }


class HttpResponse:
    def __init__(self, status=200, envelope=None):
        self.status_code = status
        self.data = json.dumps(envelope or valid_chain()).encode()
        self.headers = {"content-length": str(len(self.data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.data), chunk_size):
            yield self.data[start:start + chunk_size]


class TejHqEodTests(unittest.TestCase):
    def test_first_strike_and_pct_use_matching_eod_underlying(self):
        quote = parse_tej_chain("TCS", "DEC-26", valid_chain())
        self.assertEqual(quote.strike, Decimal("105"))
        self.assertEqual(quote.call_close, Decimal("2"))
        self.assertEqual(quote.premium_pct, Decimal("2"))
        self.assertEqual(quote.report_date, "2026-11-23")
        self.assertEqual(quote.expiry, "2026-12-29")
        self.assertEqual(quote.volume, 0)

    def test_missing_underlying_or_wrong_expiry_fails_without_yahoo_spot(self):
        payload = valid_chain()
        payload["meta"]["underlying"] = None
        with self.assertRaisesRegex(TejEodError, "underlying"):
            parse_tej_chain("TCS", "DEC-26", payload)
        with self.assertRaisesRegex(TejEodError, "No listed"):
            parse_tej_chain("TCS", "JAN-27", valid_chain())

    def test_unpriced_first_eligible_strike_never_substitutes_110(self):
        payload = valid_chain()
        payload["data"][1]["close"] = 0
        with self.assertRaisesRegex(TejEodError, "no published close"):
            parse_tej_chain("TCS", "DEC-26", payload)

    def test_duplicate_selected_strike_rejected(self):
        payload = valid_chain()
        payload["data"].append(payload["data"][1].copy())
        with self.assertRaisesRegex(TejEodError, "Duplicate"):
            parse_tej_chain("TCS", "DEC-26", payload)

    def test_auth_header_and_one_documented_path_no_broker_calls(self):
        getter = Mock(return_value=HttpResponse())
        quote = read_tej_chain(
            "TCS", "DEC-26", "tej_live_TEST_ONLY_NOT_REAL", get=getter,
        )
        self.assertEqual(quote.premium_pct, 2)
        self.assertEqual(
            getter.call_args.args,
            ("https://api.tejhq.dev/v1/fo/chain/TCS",)
        )
        self.assertEqual(
            getter.call_args.kwargs["headers"]["Authorization"],
            "Bearer tej_live_TEST_ONLY_NOT_REAL"
        )
        self.assertEqual(getter.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(getter.call_count, 1)

    def test_auth_failure_is_clear_and_never_leaks_key(self):
        getter = Mock(return_value=HttpResponse(403))
        key = "tej_live_SUPER_SECRET_EXAMPLE"
        with self.assertRaises(TejEodError) as ctx:
            read_tej_chain("TCS", "DEC-26", key, get=getter)
        self.assertIn("Retail/Pro", str(ctx.exception))
        self.assertNotIn(key, str(ctx.exception))

    def test_full_universe_is_user_started_and_paced(self):
        q, cancel = SimpleQueue(), Event()
        reads = []
        def reader(symbol, month, token):
            reads.append((symbol, month))
            return parse_tej_chain(symbol, month, valid_chain())
        scan_tej_eod(["TCS", "SBIN", "TCS"], "DEC-26", "some-valid-fake-key",
                     q, cancel, reader=reader, sleep_fn=lambda _: None)
        events = []
        while not q.empty():
            events.append(q.get())
        self.assertEqual([x[1] for x in events if x[0] == "quote"],
                         ["TCS", "SBIN"])
        self.assertEqual(events[-1], ("done", "Completed"))
        self.assertEqual(len(reads), 2)

    def test_different_dates_not_mixed_between_symbols(self):
        events, cancel = SimpleQueue(), Event()
        def reader(symbol, month, token):
            return parse_tej_chain(
                symbol, month,
                valid_chain(asof="2026-11-23" if symbol == "TCS" else "2026-11-20")
            )
        scan_tej_eod(["TCS", "SBIN"], "DEC-26", "some-valid-fake-key",
                     events, cancel, reader=reader, sleep_fn=lambda _: None)
        out = []
        while not events.empty():
            out.append(events.get())
        self.assertEqual([x[1] for x in out if x[0] == "quote"], ["TCS"])
        self.assertTrue(any("Conflicting report dates" in x[2]
                            for x in out if x[0] == "error"))

    def test_403_stops_without_flooding_requests(self):
        q, cancel = SimpleQueue(), Event()
        def reader(symbol, month, token):
            raise TejEodError("TejHQ API key needs Retail/Pro F&O access (HTTP 403)")
        scan_tej_eod(["TCS", "SBIN"], "DEC-26", "fake-long-token",
                     q, cancel, reader=reader, sleep_fn=lambda _: None)
        out = []
        while not q.empty():
            out.append(q.get())
        self.assertEqual([x[0] for x in out], ["error", "fatal"])


if __name__ == "__main__":
    unittest.main()
