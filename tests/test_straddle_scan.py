"""Offline ATM straddle screener: full 219, descending %, and safe rate limits."""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from queue import SimpleQueue
from threading import Event
from unittest.mock import patch

from dhan_visual.atm_options import AtmOptionQuote, OptionLeg, OptionDataError, IST
from dhan_visual.dhan_market import DhanUnderlying
from dhan_visual.screener import Underlying
from dhan_visual.straddle_scan import (
    StraddleRow, expiry_map_from_dhan_fno,
    rank_straddles, scan_all_straddles, straddle_from_atm,
)
from dhan_visual.straddle_panel import straddle_dataframe


def underlyings(n=219):
    return [
        DhanUnderlying(
            Underlying(f"STK{i:03}", f"STK{i:03}", {"DEC-26": 250}),
            i + 10000,
        )
        for i in range(n)
    ]


def quote(symbol, call="3", put="2.5", spot="100", bid_call="2.8", bid_put="2.3"):
    return AtmOptionQuote(
        symbol, "2026-12-29", Decimal("100"), Decimal(spot), 250,
        OptionLeg(Decimal(call), Decimal(bid_call), Decimal("3.1")),
        OptionLeg(Decimal(put), Decimal(bid_put), Decimal("2.6")),
        "Mock time",
    )


class StraddleMath(unittest.TestCase):
    def test_user_example_is_five_point_five_percent(self):
        row = straddle_from_atm(quote("STK001"))
        self.assertEqual(row.combined_ltp, Decimal("5.5"))
        self.assertEqual(row.premium_pct, Decimal("5.500"))
        self.assertEqual(row.gross_lot_premium, Decimal("1375.0"))
        self.assertEqual(row.combined_bid_pct, Decimal("5.100"))

    def test_compare_same_expiry_and_sort_highest_first(self):
        rows = [
            straddle_from_atm(quote("B", call="7", put="4")),
            straddle_from_atm(quote("A", call="3", put="2.5")),
            straddle_from_atm(quote("C", call="1", put="1")),
        ]
        ranked = rank_straddles(rows)
        self.assertEqual([r.symbol for r in ranked], ["B", "A", "C"])
        df = straddle_dataframe({r.symbol:r for r in rows})
        self.assertEqual(df["Stock"].tolist(), ["B", "A", "C"])
        self.assertEqual(df["Straddle premium (%)"].tolist(), [11.0, 5.5, 2.0])
        self.assertEqual(df["Rank"].tolist(), [1, 2, 3])

    def test_unknown_leg_must_not_receive_a_fake_score(self):
        bad = AtmOptionQuote(
            "MISSING", "2026-12-29", Decimal("100"), Decimal("100"), 250,
            OptionLeg(Decimal("3"), Decimal("2"), Decimal("4")),
            OptionLeg(None, None, None), "Mock",
        )
        with self.assertRaisesRegex(OptionDataError, "Both ATM CE and PE"):
            straddle_from_atm(bad)

    def test_bid_estimate_optional_never_substitutes_for_ltp(self):
        v = quote("STK1")
        bad_bid = AtmOptionQuote(
            v.symbol, v.expiry, v.strike, v.underlying_price, v.lot_size,
            OptionLeg(v.ce.ltp, None, None), v.pe, "mock",
        )
        row = straddle_from_atm(bad_bid)
        self.assertEqual(row.premium_pct, Decimal("5.500"))
        self.assertIsNone(row.combined_bid_pct)


class DhanExpiryMap(unittest.TestCase):
    def test_only_actual_active_stock_option_expiries(self):
        csv_bytes = (
            "EXCH_ID,SEGMENT,INSTRUMENT,UNDERLYING_SYMBOL,SM_EXPIRY_DATE\n"
            "NSE,D,OPTSTK,STK000,2026-12-29\n"
            "NSE,D,OPTSTK,STK000,2026-12-30\n"
            "NSE,D,OPTSTK,STK001,2026-12-29\n"
            "NSE,D,OPTSTK,STK001,2026-11-24\n"
            "NSE,D,FUTSTK,STK002,2026-12-29\n"
            "NSE,D,OPTIDX,NIFTY,2026-12-29\n"
        ).encode()
        result = expiry_map_from_dhan_fno(
            csv_bytes, {"STK000", "STK001", "STK002"}, "DEC-26",
            today=date(2026, 11, 24),
        )
        self.assertEqual(result, {
            "STK000": "2026-12-29", "STK001": "2026-12-29",
        })

    def test_invalid_or_expired_dates_are_not_guessed(self):
        raw = (
            "EXCH_ID,SEGMENT,INSTRUMENT,UNDERLYING_SYMBOL,SM_EXPIRY_DATE\n"
            "NSE,D,OPTSTK,STK000,2026-12-24\n"
            "NSE,D,OPTSTK,STK001,not_a_date\n"
        ).encode()
        self.assertEqual(
            expiry_map_from_dhan_fno(
                raw, {"STK000","STK001"}, "DEC-26",
                today=date(2026,12,26),
            ), {},
        )


class FullUniverseScan(unittest.TestCase):
    def test_scans_all_219_including_stocks_outside_7l_cash_filter(self):
        symbols = underlyings()
        queue, cancel = SimpleQueue(), Event()
        expiry = "2026-12-29"
        with patch("dhan_visual.straddle_scan._verified_expiry_map",
                   return_value={x.entry.symbol:expiry for x in symbols}), \
             patch("dhan_visual.straddle_scan.load_atm_options",
                   side_effect=lambda symbol, *_a, **_kw: quote(symbol)) as load:
            attempted, total = scan_all_straddles(
                symbols, "DEC-26", "1234567890", "OFFLINE_FAKE_TOKEN",
                queue, cancel,
            )
        self.assertEqual((attempted, total), (219, 219))
        self.assertEqual(load.call_count, 219)
        events=[]
        while not queue.empty():
            events.append(queue.get_nowait())
        rows={ev.symbol:ev.row for ev in events if ev.row is not None}
        self.assertEqual(len(rows),219)
        self.assertEqual(len(rank_straddles(rows)),219)
        self.assertTrue(all(
            call.kwargs.get("verified_expiry")==expiry
            for call in load.call_args_list
        ))

    def test_resume_only_unscanned_successful_stocks(self):
        queue=SimpleQueue()
        with patch("dhan_visual.straddle_scan._verified_expiry_map",return_value={}), \
             patch("dhan_visual.straddle_scan.load_atm_options",
                   side_effect=lambda symbol,*a,**kw: quote(symbol)) as load:
            attempted,total=scan_all_straddles(
                underlyings(4),"DEC-26","1234567890","OFFLINE_FAKE_TOKEN",
                queue,Event(),existing={"STK000","STK001"},
            )
        self.assertEqual((attempted,total),(4,4))
        self.assertEqual(load.call_count,2)

    def test_auth_and_rate_error_fail_closed_no_219_spam(self):
        queue=SimpleQueue()
        with patch("dhan_visual.straddle_scan._verified_expiry_map",return_value={}), \
             patch("dhan_visual.straddle_scan.load_atm_options",
                   side_effect=OptionDataError("Dhan option data HTTP 403")) as load:
            completed,total=scan_all_straddles(
                underlyings(),"DEC-26","1234567890","OFFLINE_FAKE_TOKEN",
                queue,Event(),
            )
        self.assertEqual((completed,total),(1,219))
        load.assert_called_once()
        events=[]
        while not queue.empty(): events.append(queue.get_nowait())
        self.assertTrue(any(ev.fatal for ev in events))

    def test_stop_safely_before_any_broker_request(self):
        queue, cancel = SimpleQueue(), Event()
        cancel.set()
        with patch("dhan_visual.straddle_scan.load_atm_options") as load:
            self.assertEqual(
                scan_all_straddles(underlyings(),"DEC-26",
                                   "1234567890","OFFLINE_FAKE_TOKEN",queue,cancel),
                (0,219),
            )
        load.assert_not_called()

    def test_one_malformed_stock_does_not_invent_a_percentage(self):
        def fake(symbol,*a,**kw):
            if symbol=="STK001": raise OptionDataError("Missing PE quote")
            return quote(symbol)
        queue=SimpleQueue()
        with patch("dhan_visual.straddle_scan._verified_expiry_map",return_value={}), \
             patch("dhan_visual.straddle_scan.load_atm_options",side_effect=fake):
            result=scan_all_straddles(underlyings(3),"DEC-26","1234567890",
                                      "OFFLINE_FAKE_TOKEN",queue,Event())
        self.assertEqual(result,(3,3))
        errors=[]
        rows=[]
        while not queue.empty():
            e=queue.get_nowait()
            if e.error: errors.append(e.error)
            if e.row: rows.append(e.row)
        self.assertEqual(len(rows),2)
        self.assertEqual(len(errors),1)


if __name__ == "__main__":
    unittest.main()
