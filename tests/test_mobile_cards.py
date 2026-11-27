import unittest
from decimal import Decimal
from dhan_visual.mobile_cards import card_pages, card_value


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
