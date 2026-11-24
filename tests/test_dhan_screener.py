import unittest
from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd

from dhan_visual.screener import (
    Quote, as_yahoo_symbol, choose_expiry, expiry_options,
    extract_latest_closes, normalize_expiry, parse_nse_lots, screen_rows,
)

SAMPLE = """NSE official market lots (fixture only)
UNDERLYING,SYMBOL,NOV-26,DEC-26,JAN-27
Some bank,SBIN,750,750,850
NIFTY 50,NIFTY,65,65,65
Company A,ABCAPITAL,3100,1550,1550
Company T,TCS,225,225,250
Company R,RELIANCE,500,500,500
"""


def dummy_quotes():
    return {
        "SBIN": Quote(Decimal("650"), "2026-11-24T11:00:00+05:30", "Yahoo 15m bar close"),
        "ABCAPITAL": Quote(Decimal("200"), "2026-11-24", "Yahoo daily bar close"),
        "TCS": Quote(Decimal("3000"), "2026-11-24", "Yahoo daily bar close"),
    }


class OfficialCatalogueTests(unittest.TestCase):
    def test_parse_month_lot_column_and_exclude_indices(self):
        data = parse_nse_lots(SAMPLE)
        self.assertEqual(len(data), 4)
        abc = next(item for item in data if item.symbol == "ABCAPITAL")
        self.assertEqual(abc.lots["NOV-26"], 3100)
        self.assertEqual(abc.lots["DEC-26"], 1550)
        self.assertNotIn("NIFTY", [item.symbol for item in data])

    def test_expiry_month_selection(self):
        data = parse_nse_lots(SAMPLE)
        self.assertEqual(expiry_options(data), ["NOV-26", "DEC-26", "JAN-27"])
        self.assertEqual(choose_expiry(data, datetime(2026, 11, 24, tzinfo=timezone.utc)), "NOV-26")
        self.assertEqual(choose_expiry(data, datetime(2026, 12, 4, tzinfo=timezone.utc)), "DEC-26")
        self.assertEqual(normalize_expiry("JAN-2027"), "JAN-27")

    def test_encoding_and_missing_lots(self):
        data = "\ufeffUNDERLYING,SYMBOL,NOV-26,DEC-26\nStock A,ABC,250,0\nStock B,DEF,-,300\n"
        rows = parse_nse_lots(data.encode("utf-8-sig"))
        self.assertEqual(rows[0].lots, {"NOV-26": 250})
        self.assertEqual(rows[1].lots, {"DEC-26": 300})

    def test_bad_csv_rejected(self):
        for raw in ("<html>Access denied</html>", "SYMBOL,PRICE\nTCS,1000", b"\x00\x00"):
            with self.assertRaises(ValueError):
                parse_nse_lots(raw)

    def test_duplicate_symbol_rejected(self):
        with self.assertRaises(ValueError):
            parse_nse_lots("UNDERLYING,SYMBOL,NOV-26\nA,TCS,100\nB,TCS,200\n")


class QuoteExtractionTests(unittest.TestCase):
    def test_single_symbol_frame(self):
        frame = pd.DataFrame({"Close": [100., 105.25]}, index=pd.date_range("2026-11-23", periods=2))
        self.assertEqual(extract_latest_closes(frame, ["SBIN"], "daily")["SBIN"].price, Decimal("105.25"))

    def test_multiindex_group_by_ticker(self):
        cols = pd.MultiIndex.from_tuples([("TCS.NS", "Close"), ("SBIN.NS", "Close")])
        frame = pd.DataFrame([[2900, 620], [3012.5, 655]], columns=cols,
                             index=pd.date_range("2026-11-23", periods=2))
        x = extract_latest_closes(frame, ["TCS", "SBIN"], "15m")
        self.assertEqual(x["TCS"].price, Decimal("3012.50"))
        self.assertEqual(x["SBIN"].price, Decimal("655.00"))

    def test_transposed_columns_and_absent_value(self):
        cols = pd.MultiIndex.from_tuples([("Close", "TCS.NS"), ("Close", "SBIN.NS")])
        frame = pd.DataFrame([[3000, None]], columns=cols, index=pd.date_range("2026-11-23", periods=1))
        self.assertEqual(list(extract_latest_closes(frame, ["TCS", "SBIN"], "daily")), ["TCS"])

    def test_yahoo_symbol_validation(self):
        self.assertEqual(as_yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        with self.assertRaises(ValueError):
            as_yahoo_symbol("A\nB")


class ContractFilters(unittest.TestCase):
    def setUp(self):
        self.catalogue = parse_nse_lots(SAMPLE)
        self.quotes = dummy_quotes()

    def test_price_times_lot_and_expiry_changes(self):
        nov = screen_rows(self.catalogue, "NOV-26", self.quotes)
        dec = screen_rows(self.catalogue, "DEC-26", self.quotes)
        self.assertEqual(next(x for x in nov if x["Symbol"] == "ABCAPITAL")["Contract value (₹)"], 620000.0)
        self.assertEqual(next(x for x in dec if x["Symbol"] == "ABCAPITAL")["Contract value (₹)"], 310000.0)

    def test_filter_below_5_lakhs(self):
        filtered = screen_rows(self.catalogue, "NOV-26", self.quotes, max_value=500000)
        self.assertEqual([r["Symbol"] for r in filtered], ["SBIN"])
        self.assertEqual(filtered[0]["Contract value (₹)"], 487500.)

    def test_filter_5_to_6_5_lakhs(self):
        filtered = screen_rows(self.catalogue, "NOV-26", self.quotes, min_value=500000, max_value=650000)
        self.assertEqual([r["Symbol"] for r in filtered], ["ABCAPITAL"])

    def test_ascending_descending_missing_quotes_last(self):
        asc = screen_rows(self.catalogue, "NOV-26", self.quotes)
        desc = screen_rows(self.catalogue, "NOV-26", self.quotes, descending=True)
        self.assertEqual([r["Symbol"] for r in asc], ["SBIN", "ABCAPITAL", "TCS", "RELIANCE"])
        self.assertEqual([r["Symbol"] for r in desc], ["TCS", "ABCAPITAL", "SBIN", "RELIANCE"])
        self.assertIsNone(desc[-1]["Contract value (₹)"])

    def test_company_search(self):
        self.assertEqual([r["Symbol"] for r in screen_rows(self.catalogue, "DEC-26", self.quotes, contains="company a")], ["ABCAPITAL"])

    def test_reject_invalid_contract_range(self):
        with self.assertRaises(ValueError):
            screen_rows(self.catalogue, "NOV-26", self.quotes, min_value=800000, max_value=500000)


if __name__ == "__main__":
    unittest.main()
