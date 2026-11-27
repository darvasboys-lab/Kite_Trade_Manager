import unittest

from dhan_visual.mobile_cards import card_pages, card_value, is_phone_user_agent


class MobileV2PresentationTest(unittest.TestCase):
    def test_phone_detection(self):
        for agent in ("Mozilla/5.0 (Linux; Android 14) Chrome/131 Mobile",
                      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
                      "Mozilla/5.0 (Linux; Android 14; wv)"):
            with self.subTest(agent=agent):
                self.assertTrue(is_phone_user_agent(agent))
        self.assertFalse(is_phone_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))
        self.assertFalse(is_phone_user_agent(""))

    def test_card_pagination(self):
        self.assertEqual([card_pages(n) for n in (0, 1, 8, 9, 16, 17)],
                         [1, 1, 1, 2, 2, 3])

    def test_no_fabricated_values_or_lost_basis_sign(self):
        self.assertEqual(card_value(float("nan"), "CMP (₹)"), "—")
        self.assertEqual(card_value(-1.25, "Futures vs CMP (%)"), "-1.25%")
        self.assertEqual(card_value(2.35, "5%+ OTM CE (%)"), "2.35%")
        self.assertEqual(card_value(500, "Lot size"), "500")


    def test_historical_strike_optional_and_validated(self):
        from dhan_visual.workspace_store import validate
        snapshot = {
            "saved_at": "2026-11-27T15:00:00+05:30",
            "filters": {"maximum": 7, "minimum": 2, "expiry": "NOV-26",
                        "r1_filter": "Include all stocks"},
            "rows": [{"Stock": "INFY", "CMP (₹)": 1485.0, "CE strike": 1560.0},
                     {"Stock": "TCS", "CMP (₹)": 3420.0}],
        }
        result = validate({"version": 1, "snapshot": snapshot})["snapshot"]
        self.assertEqual(result["rows"][0]["CE strike"], 1560.0)
        self.assertNotIn("CE strike", result["rows"][1])
        snapshot["rows"][0]["CE strike"] = -1
        with self.assertRaises(ValueError):
            validate({"version": 1, "snapshot": snapshot})

    def test_ce5_sort_is_descending_numeric_and_keeps_unknowns_last(self):
        import pandas as pd
        from dhan_visual.mobile_cards import sort_ce5_rows
        frame = pd.DataFrame({"Stock":["Z","B","A","C","M","N","I","L"],
            "5%+ OTM CE (%)":[2.5,10.0,10.0,8.05,None,float("nan"),float("inf"),0.5],
            "EMA 200":[999,1,2,3,4,5,6,7]})
        original = frame.copy(deep=True)
        ranked = sort_ce5_rows(frame)
        self.assertEqual(ranked["Stock"].tolist(), ["A","B","C","Z","L","I","M","N"])
        self.assertEqual(ranked.columns.tolist(), frame.columns.tolist())
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(ranked.iloc[0]["EMA 200"], 2)

    def test_ce5_sort_before_pagination_and_old_snapshots(self):
        import pandas as pd
        from dhan_visual.mobile_cards import sort_ce5_rows
        frame = pd.DataFrame({"Stock":[f"S{i:02d}" for i in range(20)],
                             "5%+ OTM CE (%)":list(range(20))})
        self.assertEqual(sort_ce5_rows(frame).iloc[:12]["Stock"].tolist(),
                         [f"S{i:02d}" for i in range(19,7,-1)])
        old = pd.DataFrame({"Stock":["B","A"]})
        self.assertEqual(sort_ce5_rows(old)["Stock"].tolist(), ["A","B"])
        self.assertTrue(sort_ce5_rows(pd.DataFrame()).empty)
