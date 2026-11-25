"""Direct native dataframe cell navigation with immutable display-row mapping."""
import unittest
from unittest.mock import patch
import pandas as pd

from dhan_visual import selective_scan as scan
from dhan_visual.stock_strategy_ui import ACTIVE
from dhan_visual.screener import Underlying
from test_selective_scan import access


class StrategyCellTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "Stock":["SBIN","TCS"],
            "5%+ OTM CE (%)":[3.2,4.4],
            "ATM Straddle Total (₹)":[None,75.0],
            "Straddle (%)":[None,7.5],
            "CMP (₹)":[1000.,1000.],
        })
        self.entries = {symbol:Underlying(symbol,symbol,{"DEC-26":100}) for symbol in self.frame.Stock}

    def event(self,index,column):
        return {"selection":{"rows":[],"columns":[],"cells":[[index,column]]}}

    def test_all_three_action_columns_and_missing_or_invalid_cells(self):
        for column,preset in scan.STRATEGY_COLUMNS.items():
            self.assertEqual(scan.strategy_cell_target(self.event(1,column),self.frame),("TCS",preset))
        self.assertEqual(scan.strategy_cell_target(self.event(0,"5%+ OTM CE (%)"),self.frame),("SBIN","Covered call"))
        for index,column in [(0,"Straddle (%)"),(0,"ATM Straddle Total (₹)"),(0,"CMP (₹)"),
                             (0,"Stock"),(2,"Straddle (%)"),(-1,"Straddle (%)"),(True,"Straddle (%)")]:
            self.assertIsNone(scan.strategy_cell_target(self.event(index,column),self.frame))
        self.assertIsNone(scan.strategy_cell_target({"selection":{"cells":[]}},self.frame))

    def test_registered_callback_opens_correct_stock_and_pauses_scan(self):
        session=access()
        key=scan._scope(session,[],"DEC-26")
        snap=scan.new_snapshot(key,"DEC-26",7,2)
        snap.update(auto_scan=True,scan_day="2026-11-25")
        state={scan.STATE:snap,"ccs_selective_page":"◎ OTM CE Home"}
        with patch.object(scan.st,"session_state",state), \
             patch.object(scan,"connected_fyers",return_value=session), \
             patch.object(scan.st,"dataframe") as display:
            scan._display(self.frame,strategy_context=(self.entries,"DEC-26",key,snap["scan_day"]))
            kwargs=display.call_args.kwargs
            self.assertEqual(kwargs["selection_mode"],"single-cell")
            callback=kwargs["on_select"]
            widget=kwargs["key"]
            # The callback keeps the exact original rendered frame even if later work changes its order.
            self.frame.iloc[1,self.frame.columns.get_loc("Stock")]="OTHER"
            state[widget]=self.event(1,"Straddle (%)")
            callback()
            self.assertEqual(state[ACTIVE]["symbol"],"TCS")
            self.assertEqual(state[ACTIVE]["preset"],"Short straddle")
            self.assertFalse(snap["auto_scan"])
            # Stale widget events cannot reopen an already active workspace.
            callback()
            self.assertEqual(state["ccs_strategy_launch_serial"],1)
            state.pop(ACTIVE)
            snap["scan_day"]="2026-11-26"
            callback()
            self.assertNotIn(ACTIVE,state)

    def test_futures_grid_does_not_register_strategy_clicks(self):
        with patch.object(scan.st,"session_state",{}),patch.object(scan.st,"dataframe") as display:
            scan._display(self.frame,futures=True)
            self.assertNotIn("on_select",display.call_args.kwargs)


if __name__=="__main__":
    unittest.main()
