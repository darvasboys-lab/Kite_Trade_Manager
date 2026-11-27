"""Thread-safe scan progress, with no broker calls and no fabricated estimates."""
from threading import Lock


def scan_percent(*, cash_done=0, cash_total=0, cash_ready=False,
                 options_done=0, options_total=0, history_done=0,
                 history_total=0, complete=False, stopped=False):
    if complete and not stopped:
        return 100
    if not cash_ready:
        return min(25, int(25 * cash_done / max(1, cash_total)))
    if options_done < options_total:
        return min(74, 25 + int(50 * options_done / max(1, options_total)))
    return min(99, 75 + int(24 * history_done / max(1, history_total)))


class ScanProgress:
    def __init__(self):
        self._lock = Lock()
        self.reset()

    def reset(self, request_id=None):
        with self._lock:
            self._data = dict(request_id=request_id, percent=0, stage="idle",
                              cash_done=0,cash_total=0,cash_received=0,
                              options_done=0,options_total=0,history_done=0,
                              history_total=0,cash_ready=False,complete=False,
                              stopped=False,missing_options=0,missing_history=0)

    def update(self, **fields):
        with self._lock:
            self._data.update(fields)
            d=self._data
            value=scan_percent(**{k:d[k] for k in ("cash_done","cash_total","cash_ready","options_done","options_total","history_done","history_total","complete","stopped")})
            d["percent"]=max(d["percent"],value)
            if d["stopped"]:
                d["percent"]=min(99,d["percent"])

    def snapshot(self):
        with self._lock:
            return self._data.copy()
