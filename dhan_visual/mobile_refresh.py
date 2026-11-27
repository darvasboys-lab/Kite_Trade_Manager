"""Mobile scan staging. Worker never accesses Streamlit session state or disk."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from datetime import datetime
import time

from .ce_eod_remote import IST
from .fyers_readonly import FyersDataError

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ccs-refresh")
LOCK = Lock()
CURRENT = None
JOB = "ccs_mobile_refresh_job"



def fetch_cash_batch(access, batch, cancel, check, fetch, report):
    """Retry only temporary transport/5xx failures or missing quotes, once.
    All requests still pass through the shared FYERS rate gate.
    """
    result = {}
    pending = list(batch)
    for attempt in range(2):
        check()
        try:
            result.update(fetch(access, pending))
        except FyersDataError as exc:
            message = str(exc)
            temporary = (message == "FYERS market-data service is unreachable."
                         or message in {f"FYERS market data HTTP {code}."
                                        for code in (500, 502, 503, 504)})
            if not temporary or attempt:
                raise
            report("Temporary connection error · retrying in 5 seconds")
        else:
            pending = [symbol for symbol in batch if symbol not in result]
            if not pending:
                return result
            if attempt:
                break
            report(f"{len(pending)} missing stock prices · retrying only missing symbols")
        if cancel.wait(5):
            check()
        check()
    return result


def collect(access, available, filters, key, cancel, report):
    from . import selective_scan as s
    from .fyers_readonly import fetch_cash_quotes
    snap = s.new_snapshot(key, filters["expiry"], filters["maximum"],
                          filters["minimum"], filters["r1_filter"])
    today = datetime.now(IST).date()
    snap["scan_day"] = today.isoformat()
    def check():
        if cancel.is_set() or not access.valid() or datetime.now(IST).date() != today:
            raise FyersDataError("Refresh cancelled or session expired. Previous data retained.")
        if snap["stopped"]:
            raise FyersDataError("Refresh stopped by FYERS. Previous data retained.")
    symbols = list(dict.fromkeys(item.entry.symbol for item in available))
    if not symbols:
        raise FyersDataError("No verified instruments for this expiry.")
    total_batches = (len(symbols) + 49) // 50
    for i in range(0, len(symbols), 50):
        check()
        batch = symbols[i:i+50]
        number = i // 50 + 1
        percent = 20 * i // len(symbols)
        report(percent, f"Stock prices: batch {number}/{total_batches} · {len(snap['cash'])}/{len(symbols)} received")
        snap["cash"].update(fetch_cash_batch(
            access, batch, cancel, check, fetch_cash_quotes,
            lambda stage: report(percent, f"Stock prices: batch {number}/{total_batches} · {stage}")))
        report(20 * min(i+len(batch),len(symbols)) // len(symbols),
               f"Stock prices: {len(snap['cash'])}/{len(symbols)} received")
    check()
    missing = [symbol for symbol in symbols if symbol not in snap["cash"]]
    if missing:
        raise FyersDataError(f"{len(missing)} stock prices unavailable: {', '.join(missing[:8])}. Previous data retained.")
    snap["cash_at"] = s._stamp()
    candidates = s.pending_option_symbols(available, snap)
    total = len(candidates)
    for start in range(0, total, s.BATCH):
        began = time.monotonic()
        for symbol in candidates[start:start+s.BATCH]:
            check()
            if s.next_option_batch(access, available, snap, limit=1) != 1:
                check()
                raise FyersDataError("Option-chain scan incomplete.")
            check()
            # A valid request may have no eligible OTM call; a wholly absent
            # parsed chain cannot be treated as a successful refresh.
            if symbol not in snap["calls"] and symbol not in snap["straddles"]:
                raise FyersDataError("An eligible option chain returned no usable quotes. Previous data retained.")
            report(20 + 70 * len(snap["attempted"]) // max(1,total),
                   f"Option chains {len(snap['attempted'])}/{total}")
        if start+s.BATCH < total:
            if cancel.wait(max(0, s.BATCH_INTERVAL_SECONDS-(time.monotonic()-began))):
                check()
    check()
    names = s.pending_r1_symbols(available, snap)
    total_history = len(names)
    report(90, "Checking shortlisted technical data")
    for symbol in names:
        check()
        if s.load_shortlist_history(access, snap, [symbol], today=today) != 1:
            check()
            raise FyersDataError("Technical scan incomplete.")
        report(90 + 9 * len(snap["levels"]) // max(1,total_history),
               f"Technical data {len(snap['levels'])}/{total_history}")
    check()
    if s.automatic_work_pending(available, snap):
        raise FyersDataError("Refresh incomplete.")
    snap["complete"] = True
    snap["completed_at"] = datetime.now(IST).isoformat(timespec="seconds")
    report(100, "All requested stages completed")
    return snap


def start(access, available, filters, key):
    """One process-wide worker, no duplicate queued scans."""
    global CURRENT
    with LOCK:
        if CURRENT is not None and not CURRENT.done():
            return None
        cancel = Event()
        job = {"cancel":cancel, "key":key, "filters":dict(filters),
               "progress":(0,"Preparing refresh")}
        def report(percent, message):
            job["progress"] = (percent, message)
        job["future"] = POOL.submit(collect, access, list(available), dict(filters),
                                    key, cancel, report)
        CURRENT = job["future"]
        return job
