"""Offline tests for free CE5 EOD report detection in local Windows Downloads."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import io
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from dhan_visual.ce_eod_local import recent_nse_fo_reports
from dhan_visual.nse_eod_ce import NseEodError, parse_nse_fo_eod


def daily_report():
    columns = ("BizDt", "FinInstrmTp", "TckrSymb", "XpryDt",
               "StrkPric", "OptnTp", "ClsPric", "UndrlygPric", "TtlTradgVol")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for strike, close, volume in [("104", "4", "10"), ("105", "2", "8"), ("110", "1", "1")]:
        writer.writerow(("2026-11-23", "STO", "TCS", "2026-12-29", strike,
                         "CE", close, "100", volume))
    return out.getvalue().encode("utf-8")


def save_nse_zip(folder, date_token, data=None):
    path = Path(folder) / f"BhavCopy_NSE_FO_0_0_0_{date_token}_F_0000.csv.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        zf.writestr(path.name[:-4], daily_report() if data is None else data)
    return path


class LocalCeReportTests(unittest.TestCase):
    def test_finds_latest_original_filename_in_downloads(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            previous = save_nse_zip(directory, "20261120")
            latest = save_nse_zip(directory, "20261123")
            (directory / "holiday-notes.csv").write_text("secret", encoding="utf-8")
            self.assertEqual(
                recent_nse_fo_reports(directory, today=date(2026, 11, 25)),
                [latest, previous],
            )

    def test_ignores_future_old_nonofficial_and_empty_files(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            valid = save_nse_zip(directory, "20261123")
            save_nse_zip(directory, "20261126")
            save_nse_zip(directory, "20261110")
            (directory / "BhavCopy_NSE_FO_0_0_0_20261122_F_0000.csv.zip").touch()
            (directory / "secret-account.txt").write_text("sensitive", encoding="utf-8")
            self.assertEqual(recent_nse_fo_reports(directory, today=date(2026, 11, 25)),
                             [valid])

    def test_reports_validate_actual_business_date_and_dec_ce_math(self):
        with TemporaryDirectory() as tmp:
            path = save_nse_zip(tmp, "20261123")
            snapshot = parse_nse_fo_eod(path.read_bytes())
            self.assertEqual(snapshot.report_date, "2026-11-23")
            ce = snapshot.quotes["DEC-26"]["TCS"]
            self.assertEqual(ce.expiry, "2026-12-29")
            self.assertEqual(str(ce.strike), "105")
            self.assertEqual(str(ce.premium_pct), "2.00")
            self.assertEqual(ce.volume, 8)

    def test_tampered_empty_report_raises(self):
        with TemporaryDirectory() as tmp:
            path = save_nse_zip(tmp, "20261123", b"bad,data\n1,2")
            with self.assertRaisesRegex(NseEodError, "required headers"):
                parse_nse_fo_eod(path.read_bytes())


if __name__ == "__main__":
    unittest.main()
