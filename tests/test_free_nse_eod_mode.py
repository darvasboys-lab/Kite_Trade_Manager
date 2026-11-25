"""Offline regression checks for persistent, free NSE F&O EOD data."""
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dhan_visual.ce_eod_store import (
    eod_cash_quotes, load_local_eod, read_verified_report,
    save_verified_report,
)
from dhan_visual.nse_eod_ce import NseEodError, parse_nse_fo_eod
from test_ce_eod_local import daily_report, save_nse_zip


class FreeNseEodModeTests(unittest.TestCase):
    def test_genuine_fo_underlying_reference_and_ce_share_report_date(self):
        snap = parse_nse_fo_eod(daily_report())
        self.assertEqual(snap.report_date, "2026-11-23")
        self.assertEqual(snap.underlyings["TCS"], Decimal("100"))
        self.assertEqual(
            eod_cash_quotes(snap)["TCS"].source,
            "NSE F&O EOD underlying close",
        )
        self.assertEqual(
            eod_cash_quotes(snap)["TCS"].price, Decimal("100")
        )
        self.assertEqual(
            snap.quotes["DEC-26"]["TCS"].premium_pct, Decimal("2")
        )

    def test_true_atm_straddle_uses_exact_same_expiry_strike_and_underlying(self):
        from dhan_visual.dhan_market import DhanUnderlying
        from dhan_visual.screener import Underlying
        from dhan_visual.eod_straddle_ui import eod_straddle_rows
        report = daily_report() + (
            b"2026-11-23,STO,TCS,2026-12-29,100,CE,4,100,3\\r\\n"
            b"2026-11-23,STO,TCS,2026-12-29,100,PE,3,100,0\\r\\n"
        )
        snap = parse_nse_fo_eod(report)
        pair = snap.straddles["DEC-26"]["TCS"]
        self.assertEqual(pair.strike, Decimal("100"))
        self.assertEqual(pair.premium_pct, Decimal("7"))
        self.assertEqual(pair.call_volume, 3)
        self.assertEqual(pair.put_volume, 0)
        catalogue = [
            DhanUnderlying(Underlying("TCS", "TCS", {"DEC-26": 225}), 100),
        ]
        rows = eod_straddle_rows(catalogue, "DEC-26", snap)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Gross premium × lot (₹)"], 1575.0)
        self.assertEqual(rows[0]["Liquidity"], "No trades in a leg")

    def test_incomplete_atm_pair_never_uses_neighboring_strike(self):
        report = daily_report() + (
            b"2026-11-23,STO,TCS,2026-12-29,100,PE,3,100,5\\r\\n"
        )
        snap = parse_nse_fo_eod(report)
        self.assertFalse(snap.straddles.get("DEC-26", {}))

    def test_import_persists_across_app_sessions_and_does_not_downgrade(self):
        with TemporaryDirectory() as temp:
            file = Path(temp) / "cache" / "nse_fo_eod_last.bin"
            payload = daily_report()
            report = save_verified_report(payload, file)
            self.assertEqual(report.report_date, "2026-11-23")
            self.assertEqual(read_verified_report(file), report)
            older = payload.replace(b"2026-11-23", b"2026-11-22")
            report2 = save_verified_report(older, file)
            self.assertEqual(report2.report_date, "2026-11-23")
            self.assertEqual(read_verified_report(file), report)
            self.assertNotIn(b"2026-11-22", file.read_bytes())

    def test_corrupt_and_symlink_cache_fail_closed(self):
        with TemporaryDirectory() as temp:
            location = Path(temp) / "cache.bin"
            location.write_bytes(b"not,a,report")
            self.assertIsNone(read_verified_report(location))
            with self.assertRaises(NseEodError):
                save_verified_report(b"garbage", location)
            self.assertEqual(location.read_bytes(), b"not,a,report")
            link = Path(temp) / "link.bin"
            try:
                link.symlink_to(location)
            except (OSError, NotImplementedError):
                return
            with self.assertRaisesRegex(NseEodError, "symbolic"):
                save_verified_report(daily_report(), link)

    def test_downloads_are_validated_and_cached_without_network(self):
        with TemporaryDirectory() as temp:
            downloads = Path(temp) / "Downloads"
            downloads.mkdir()
            file = save_nse_zip(downloads, "20261123")
            cache = Path(temp) / "cache.bin"
            # Patch only the local filename enumerator to avoid test-date drift.
            with patch("dhan_visual.ce_eod_store.recent_nse_fo_reports",
                       return_value=[file]):
                snap, label = load_local_eod(cache, downloads=downloads)
            self.assertIsNotNone(snap)
            self.assertIn("Downloads/", label)
            self.assertEqual(read_verified_report(cache), snap)
            snap2, _ = load_local_eod(cache, downloads=Path(temp) / "missing")
            self.assertEqual(snap2, snap)

    def test_wrong_report_date_does_not_overwrite_existing_cache(self):
        with TemporaryDirectory() as temp:
            downloads = Path(temp) / "Downloads"
            downloads.mkdir()
            misnamed = save_nse_zip(downloads, "20261124")
            cache = Path(temp) / "cache.bin"
            with patch("dhan_visual.ce_eod_store.recent_nse_fo_reports",
                       return_value=[misnamed]):
                snap, _ = load_local_eod(cache, downloads=downloads)
            self.assertIsNone(snap)
            self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
