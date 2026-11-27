import unittest
from decimal import Decimal
from dhan_visual.mobile_cards import card_pages, card_value, card_fields, phone_user_agent


class MobileCardsTests(unittest.TestCase):
    def test_page_count(self):
        self.assertEqual([card_pages(i) for i in (0, 1, 8, 9, 219)], [1, 1, 1, 2, 28])

    def test_missing_and_numeric(self):
        self.assertEqual(card_value(float("nan"), "CMP (₹)"), "—")
        self.assertEqual(card_value(None, "Straddle (%)"), "—")
        self.assertEqual(card_value(Decimal("2.125"), "5%+ OTM CE (%)"), "2.12%")
        self.assertEqual(card_value(1200, "CMP (₹)"), "₹1,200.00")
        self.assertEqual(card_value(500, "Lot size"), "500")
        self.assertEqual(card_value("✓", "> R1"), "✓")


    def test_phone_detection_and_desktop_preserved(self):
        self.assertTrue(phone_user_agent("Mozilla/5.0 (Linux; Android 14; Mobile)"))
        self.assertTrue(phone_user_agent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"))
        self.assertFalse(phone_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))
        self.assertFalse(phone_user_agent(""))

    def test_card_fields_escape_and_preserve_missing(self):
        html = card_fields({"CMP (₹)": 1200, "> R1": "<script>alert(1)</script>", "Straddle (%)": None},
                           [("CMP (₹)", "CMP"), ("> R1", "R1"), ("Straddle (%)", "ATM"), ("absent", "Absent")])
        self.assertIn("₹1,200.00", html)
        self.assertIn("—", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("Absent", html)
