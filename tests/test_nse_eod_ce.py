"""Offline tests for the safe user-uploaded NSE daily CE +5% fallback."""
import csv
import io
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from dhan_visual.nse_eod_ce import (
    NseEodError, parse_nse_fo_eod,
)


HEADERS = [
    "BizDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric",
    "OptnTp", "ClsPric", "UndrlygPric", "TtlTradgVol",
]


def row(symbol="TCS", expiry="2026-11-27", strike="105",
        kind="CE", price="2.50", spot="100", volume="10",
        instrument="STO", business="2026-11-25"):
    return dict(zip(HEADERS, [
        business, instrument, symbol, expiry, strike,
        kind, price, spot, volume,
    ]))


def make_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def make_zip(rows):
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as zf:
        zf.writestr("BhavCopy_NSE_FO_0_0_0_20261125_F_0000.csv", make_csv(rows))
    return out.getvalue()


class NseEodCeTests(unittest.TestCase):
    def test_first_listed_5_percent_strike_is_selected(self):
        data = make_zip([
            row(strike="104", price="4.25"),
            row(strike="105", price="2.5"),
            row(strike="110", price="1.1"),
            row(strike="105", kind="PE", price="5"),
            row(strike="105", instrument="IDO"),
        ])
        snap = parse_nse_fo_eod(data)
        self.assertEqual(snap.report_date, "2026-11-25")
        quote = snap.quotes["NOV-26"]["TCS"]
        self.assertEqual(str(quote.strike), "105")
        self.assertEqual(str(quote.premium_pct), "2.500")
        self.assertEqual(quote.call_close, 2.5)
        self.assertEqual(quote.expiry, "2026-11-27")

    def test_zero_volume_is_explicit_untraded_eod_reference_not_live_ltp(self):
        snap = parse_nse_fo_eod(make_csv([
            row(symbol="AAA", strike="105", volume="0", price="3"),
            row(symbol="AAA", strike="110", volume="12", price="2"),
            row(symbol="BBB", strike="105", volume="4", price="1"),
            row(symbol="CCC", strike="105", volume="0", price="0"),
        ]))
        self.assertEqual(snap.quotes["NOV-26"]["AAA"].volume, 0)
        self.assertEqual(snap.quotes["NOV-26"]["AAA"].strike, 105)
        self.assertEqual(snap.quotes["NOV-26"]["AAA"].premium_pct, 3)
        self.assertNotIn("CCC", snap.quotes["NOV-26"])
        self.assertIn("BBB", snap.quotes["NOV-26"])

    def test_inconsistent_underlying_is_not_guessed(self):
        snap = parse_nse_fo_eod(make_csv([
            row(symbol="AAA", strike="105", spot="100"),
            row(symbol="AAA", strike="110", spot="101"),
            row(symbol="BBB", strike="105", spot="100"),
        ]))
        self.assertNotIn("AAA", snap.quotes["NOV-26"])
        self.assertIn("BBB", snap.quotes["NOV-26"])

    def test_multiple_same_month_expiries_are_omitted(self):
        snap = parse_nse_fo_eod(make_csv([
            row(symbol="AAA", expiry="2026-11-26"),
            row(symbol="AAA", expiry="2026-11-27"),
            row(symbol="BBB", expiry="2026-11-27"),
        ]))
        self.assertNotIn("AAA", snap.quotes["NOV-26"])
        self.assertIn("BBB", snap.quotes["NOV-26"])

    def test_expired_and_future_month_not_substituted(self):
        snap = parse_nse_fo_eod(make_csv([
            row(symbol="AAA", expiry="2026-11-24"),
            row(symbol="BBB", expiry="2026-12-29"),
        ]))
        self.assertNotIn("AAA", snap.quotes.get("NOV-26", {}))
        self.assertIn("BBB", snap.quotes["DEC-26"])

    def test_not_an_options_bhavcopy_is_rejected(self):
        with self.assertRaisesRegex(NseEodError, "required headers"):
            parse_nse_fo_eod(b"a,b,c\n1,2,3\n")
        with self.assertRaisesRegex(NseEodError, "No stock option"):
            parse_nse_fo_eod(make_csv([row(instrument="STF")]))

    def test_mixed_report_dates_are_rejected(self):
        with self.assertRaisesRegex(NseEodError, "multiple business dates"):
            parse_nse_fo_eod(make_csv([
                row(symbol="AAA", business="2026-11-25"),
                row(symbol="BBB", business="2026-11-24"),
            ]))

    def test_untrusted_large_upload_is_rejected(self):
        with self.assertRaisesRegex(NseEodError, "exceeds"):
            parse_nse_fo_eod(b"x" * (16 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
