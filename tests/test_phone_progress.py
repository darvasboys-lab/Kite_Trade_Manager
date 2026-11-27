import unittest
from dhan_visual.phone_progress import ScanProgress, scan_percent

class PhoneProgressTests(unittest.TestCase):
    def test_stages(self):
        self.assertEqual(scan_percent(cash_total=200,cash_done=0),0)
        self.assertEqual(scan_percent(cash_total=200,cash_done=100),12)
        self.assertEqual(scan_percent(cash_ready=True,options_total=100,options_done=50),50)
        self.assertEqual(scan_percent(cash_ready=True,options_total=100,options_done=100,history_total=10,history_done=5),87)
        self.assertEqual(scan_percent(cash_ready=True,options_total=100,options_done=100,history_total=10,history_done=10),99)
        self.assertEqual(scan_percent(complete=True),100)
        self.assertLess(scan_percent(complete=True,stopped=True),100)

    def test_monotonic_progress_and_reset(self):
        p=ScanProgress()
        p.reset("first")
        p.update(cash_total=200,cash_done=50,cash_received=49,stage="cash")
        self.assertEqual(p.snapshot()["percent"],6)
        p.update(cash_ready=True,options_total=40,options_done=20,stage="options")
        self.assertEqual(p.snapshot()["percent"],50)
        p.update(options_done=40,history_total=5,history_done=4,stage="history")
        previous=p.snapshot()["percent"]
        p.update(history_total=10)
        self.assertEqual(p.snapshot()["percent"],previous)
        p.update(complete=True,stage="complete")
        self.assertEqual(p.snapshot()["percent"],100)
        p.update(stopped=True,stage="stopped")
        self.assertEqual(p.snapshot()["percent"],99)
        p.reset("second")
        self.assertEqual(p.snapshot()["percent"],0)
        self.assertEqual(p.snapshot()["request_id"],"second")

    def test_read_returns_copy(self):
        p=ScanProgress()
        snap=p.snapshot()
        snap["percent"]=100
        self.assertEqual(p.snapshot()["percent"],0)

if __name__=="__main__":
    unittest.main()
