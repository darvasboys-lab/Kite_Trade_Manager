"""5%-above-CMP CE must reuse the existing read-only option-chain payload."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from dhan_visual.atm_options import AtmOptionQuote, OptionLeg, parse_atm_option_chain
from dhan_visual.straddle_scan import (
    otm_call_5_from_atm, straddle_from_atm, ScanUpdate,
)
from dhan_visual.straddle_panel import straddle_dataframe


def chain(spot="100", extras=None):
    oc={
        "100": {
            "ce": {"last_price": 3, "top_bid_price": 2.8},
            "pe": {"last_price": 2.5, "top_bid_price": 2.3},
        },
        "102.5": {"ce": {"last_price": 2.2}},
        "105": {"ce": {"last_price": 2, "top_bid_price": 1.95}},
        "110": {"ce": {"last_price": 1.2}},
    }
    if extras:
        oc.update(extras)
    return {"status":"success","data":{"last_price":spot,"oc":oc}}


class OtmCallPremiumTests(unittest.TestCase):
    def test_exact_plus_5pct_strike_user_example_two_percent(self):
        atm=parse_atm_option_chain(chain(),"TCS","2026-12-29",250,"mock")
        self.assertEqual(atm.otm_5_strike,Decimal("105"))
        self.assertEqual(atm.otm_5_ce.ltp,Decimal("2"))
        otm=otm_call_5_from_atm(atm)
        self.assertIsNotNone(otm)
        self.assertEqual(otm.premium_pct,Decimal("2.00"))
        self.assertEqual(otm.underlying_ltp,Decimal("100"))
        self.assertEqual(straddle_from_atm(atm).premium_pct,Decimal("5.500"))

    def test_missing_exact_105_uses_first_real_strike_above_not_below(self):
        payload=chain()
        del payload["data"]["oc"]["105"]
        atm=parse_atm_option_chain(payload,"TCS","2026-12-29",250,"mock")
        self.assertEqual(atm.otm_5_strike,Decimal("110"))
        self.assertEqual(otm_call_5_from_atm(atm).premium_pct,Decimal("1.200"))

    def test_missing_otm_strike_does_not_fabricate_price(self):
        payload=chain()
        del payload["data"]["oc"]["105"]
        del payload["data"]["oc"]["110"]
        atm=parse_atm_option_chain(payload,"TCS","2026-12-29",250,"mock")
        self.assertIsNone(atm.otm_5_strike)
        self.assertIsNone(otm_call_5_from_atm(atm))
        self.assertEqual(straddle_from_atm(atm).premium_pct,Decimal("5.500"))

    def test_nearest_ge_target_with_zero_ltp_returns_unavailable(self):
        payload=chain()
        payload["data"]["oc"]["105"]["ce"]["last_price"]=0
        atm=parse_atm_option_chain(payload,"TCS","2026-12-29",250,"mock")
        self.assertEqual(atm.otm_5_strike,Decimal("105"))
        self.assertIsNone(otm_call_5_from_atm(atm))
        # Must NOT silently jump to 110 when nearest valid strike is unquoted.
        self.assertIsNotNone(straddle_from_atm(atm))

    def test_both_values_from_single_chain_and_straddle_table_numeric(self):
        atm=parse_atm_option_chain(chain(),"TCS","2026-12-29",250,"mock")
        row=straddle_from_atm(atm)
        otm=otm_call_5_from_atm(atm)
        df=straddle_dataframe({"TCS":row},{"TCS":otm})
        self.assertEqual(df["Straddle premium (%)"].tolist(),[5.5])
        self.assertEqual(df["5%+ CE premium (%)"].tolist(),[2.0])
        self.assertEqual(df["5%+ CE strike (₹)"].tolist(),[105.0])

    def test_legacy_constructed_atm_without_otm_is_still_valid(self):
        atm=AtmOptionQuote(
            "TCS","2026-12-29",Decimal("100"),Decimal("100"),250,
            OptionLeg(Decimal("3"),None,None),
            OptionLeg(Decimal("2.5"),None,None),"mock",
        )
        self.assertIsNone(otm_call_5_from_atm(atm))
        self.assertEqual(straddle_from_atm(atm).premium_pct,Decimal("5.500"))


if __name__ == "__main__":
    unittest.main()
