"""Speed/caching regression tests for F&O universe; all network calls mocked."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dhan_visual.dhan_market import DhanDataError, DhanUnderlying
from dhan_visual.fast_universe import (
    EQ_SEGMENT_URL, FNO_SEGMENT_URL, COMPACT_MASTER_URL, MAX_CACHE_AGE_SECONDS,
    resolve_equity_ids, _download_csv,
    _read_cache, _write_cache, load_fast_catalogue,
    map_nse_lots_to_cash_security_ids, parse_dhan_equities, parse_dhan_fno_segment,
)
from dhan_visual.screener import Underlying

EQ_HEADER = "EXCH_ID,SEGMENT,SECURITY_ID,INSTRUMENT,INSTRUMENT_TYPE,SYMBOL_NAME,SERIES,UNDERLYING_SECURITY_ID,UNDERLYING_SYMBOL,LOT_SIZE,SM_EXPIRY_DATE\n"
FNO_HEADER = EQ_HEADER
LOT_HEADER = "COMPANY NAME,SYMBOL,NOV-26,DEC-26\n"


def make_inputs(n=125):
    eq = [EQ_HEADER]
    lots = [LOT_HEADER]
    derivatives = [FNO_HEADER]
    for idx in range(1, n + 1):
        symbol = f"FO{idx:04d}"
        security = 100000 + idx
        eq.append(f"NSE,E,{security},EQUITY,EQ,{symbol},EQ,,,,\n")
        lots.append(f"Company {idx},{symbol},250,300\n")
        derivatives.append(
            f"NSE,D,{200000+idx},OPTSTK,STKOPT,{symbol}26NOVCE,,{security},{symbol},250,2026-11-26\n"
        )
    eq.append("NSE,E,999999,EQUITY,EQ,OTHER,BE,,,,\n")
    return "".join(eq).encode(), "".join(lots).encode(), "".join(derivatives).encode()


class FastUniverseTests(unittest.TestCase):
    def test_segment_cash_equity_only(self):
        eq, lots, fno = make_inputs(110)
        mapping = parse_dhan_equities(eq)
        self.assertEqual(len(mapping), 110)
        self.assertNotIn("OTHER", mapping)
        self.assertEqual(mapping["FO0001"], 100001)

    def test_nse_fast_join_and_expiry_lots(self):
        eq, lots, _ = make_inputs(105)
        mapping = parse_dhan_equities(eq)
        data = map_nse_lots_to_cash_security_ids(lots, mapping)
        self.assertEqual(len(data), 105)
        self.assertEqual(data[0].entry.lots, {"NOV-26":250, "DEC-26":300})
        self.assertEqual(data[0].equity_security_id, 100001)

    def test_segment_fno_fallback(self):
        eq, _, fno = make_inputs(105)
        data = parse_dhan_fno_segment(fno, parse_dhan_equities(eq))
        self.assertEqual(len(data), 105)
        self.assertEqual(data[0].entry.lots, {"NOV-26":250})

    def test_public_cache_survives_restart_without_network(self):
        eq, lots, _ = make_inputs(120)
        records = map_nse_lots_to_cash_security_ids(lots, parse_dhan_equities(eq))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private_cache" / "universe.json"
            _write_cache(records, "NSE+Dhan test", path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("access-token", text)
            self.assertNotIn("client-id", text)
            with patch("dhan_visual.fast_universe._download_csv", side_effect=AssertionError("network unexpectedly used")):
                actual, label = load_fast_catalogue(cache_path=path)
            self.assertEqual(len(actual),120)
            self.assertIn("Local cache", label)

    def test_prefers_two_small_files_before_fno(self):
        eq, lots, _ = make_inputs(125)
        requests = []
        def download(url, cap, **kwargs):
            requests.append(url)
            if url == EQ_SEGMENT_URL:
                return eq
            if "fo_mktlots" in url:
                return lots
            raise AssertionError("Global/FNO master should not be called")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("dhan_visual.fast_universe._download_csv", side_effect=download):
                rows, source = load_fast_catalogue(cache_path=Path(tmp)/"universe.json")
        self.assertEqual(len(rows), 125)
        self.assertEqual(len(requests), 1)
        self.assertTrue(all(x.equity_security_id == 0 for x in rows))
        self.assertIn("NSE official", source)

    def test_fallback_is_segment_only_when_nse_unavailable(self):
        eq, _, fno = make_inputs(125)
        requests = []
        def download(url, cap, **kwargs):
            requests.append(url)
            if url == EQ_SEGMENT_URL:
                return eq
            if url == FNO_SEGMENT_URL:
                return fno
            raise DhanDataError("NSE lot blocked")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("dhan_visual.fast_universe._download_csv", side_effect=download):
                rows, source = load_fast_catalogue(cache_path=Path(tmp)/"universe.json")
        self.assertEqual(len(rows), 125)
        self.assertEqual(requests[-1], FNO_SEGMENT_URL)
        self.assertIn("segment files", source)

    def test_stale_cache_is_explicit_after_failure(self):
        eq, lots, _ = make_inputs(105)
        data = map_nse_lots_to_cash_security_ids(lots, parse_dhan_equities(eq))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"universe.json"
            _write_cache(data, "old master", path)
            raw=json.loads(path.read_text())
            raw["fetched_at"]=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
            path.write_text(json.dumps(raw),encoding="utf-8")
            with patch("dhan_visual.fast_universe._download_csv", side_effect=DhanDataError("offline")), \
                 patch("dhan_visual.dhan_market.fetch_dhan_master", side_effect=DhanDataError("offline")):
                result,label=load_fast_catalogue(cache_path=path)
            self.assertEqual(len(result),105)
            self.assertIn("STALE CACHE",label)

    def test_automatic_public_master_fallback_without_upload(self):
        eq, lots, _ = make_inputs(115)
        records = map_nse_lots_to_cash_security_ids(lots, parse_dhan_equities(eq))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.json"
            with patch("dhan_visual.fast_universe._download_csv", side_effect=DhanDataError("segment blocked")), \
                 patch("dhan_visual.dhan_market.fetch_dhan_master", return_value=(records, "test")) as public:
                actual, source = load_fast_catalogue(cache_path=path)
            self.assertEqual(len(actual), 115)
            self.assertIn("Dhan official detailed master", source)
            public.assert_called_once()
            self.assertIsNotNone(_read_cache(path))

    def test_nse_list_appears_with_no_dhan_service(self):
        _, lots, _ = make_inputs(125)
        called = []
        def download(url, cap, **kwargs):
            called.append(url)
            if "fo_mktlots" in url:
                return lots
            raise AssertionError("NSE-only list must not depend on Dhan")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("dhan_visual.fast_universe._download_csv", side_effect=download):
                stocks, source = load_fast_catalogue(cache_path=Path(tmp) / "universe.json")
                self.assertEqual(len(stocks), 125)
                self.assertTrue(all(x.equity_security_id == 0 for x in stocks))
                self.assertEqual(len(called), 1)
                self.assertIn("NSE official F&O lots", source)
                again, label = load_fast_catalogue(cache_path=Path(tmp) / "universe.json")
                self.assertEqual(len(again), 125)
                self.assertIn("Local cache", label)

    def test_cash_id_compact_fallback_is_on_demand(self):
        eq, _, _ = make_inputs(105)
        called = []
        def get_source(url, cap, **kwargs):
            called.append(url)
            if url == EQ_SEGMENT_URL:
                raise DhanDataError("HTTP 403")
            if url == COMPACT_MASTER_URL:
                return eq
            raise AssertionError("Detailed source must not be needed")
        with patch("dhan_visual.fast_universe._download_csv", side_effect=get_source):
            ids = resolve_equity_ids()
        self.assertEqual(len(ids), 105)
        self.assertEqual(ids["FO0001"], 100001)
        self.assertEqual(called, [EQ_SEGMENT_URL, COMPACT_MASTER_URL])

    @patch("requests.get")
    def test_https_redirect_for_public_csv_is_allowed(self, get):
        response = unittest.mock.MagicMock()
        response.status_code = 200
        response.url = "https://public-files.example-cdn.com/signed-download.csv"
        response.iter_content.return_value = [b"EXCH_ID,SEGMENT\nNSE,E\n"]
        get.return_value = response
        data = _download_csv(EQ_SEGMENT_URL, 1_000)
        self.assertIn(b"NSE,E", data)
        get.assert_called_once()
        response.close.assert_called_once()

    @patch("requests.get")
    def test_http_errors_have_source_and_status(self, get):
        response = unittest.mock.MagicMock()
        response.status_code = 403
        response.url = EQ_SEGMENT_URL
        get.return_value = response
        with self.assertRaisesRegex(DhanDataError, r"api.dhan.co: HTTP 403"):
            _download_csv(EQ_SEGMENT_URL, 1_000)

    def test_reject_malformed_cache_and_no_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"universe.json"
            path.write_text('{"schema_version":2,"stocks":[{"symbol":"FAKE"}]}')
            self.assertIsNone(_read_cache(path))
            with patch("dhan_visual.fast_universe._download_csv", side_effect=DhanDataError("offline")), \
                 patch("dhan_visual.dhan_market.fetch_dhan_master", side_effect=DhanDataError("offline")):
                with self.assertRaises(DhanDataError):
                    load_fast_catalogue(cache_path=path)


if __name__ == "__main__":
    unittest.main()
