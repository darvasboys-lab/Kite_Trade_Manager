"""Process-wide logical provider requests; no tokens, URLs or response bodies stored."""
from collections import deque, Counter
from datetime import datetime, timezone, timedelta
from threading import RLock
import time

LOCK = RLock()
EVENTS = deque()
COUNTS = Counter()
DAY = ""
LAST = {}
IST = timezone(timedelta(hours=5,minutes=30))


def _day():
    global DAY
    today=datetime.now(IST).date().isoformat()
    if today!=DAY:
        DAY=today
        COUNTS.clear()
        LAST.clear()
        EVENTS.clear()


def record(provider, event="request"):
    with LOCK:
        _day()
        COUNTS[provider,event]+=1
        now=time.monotonic()
        if event=="request":
            EVENTS.append((now,provider))
        elif event=="success":
            LAST[provider]=datetime.now(IST).strftime("%H:%M:%S IST")
        while EVENTS and EVENTS[0][0]<=now-60:
            EVENTS.popleft()


def stats():
    with LOCK:
        _day()
        now=time.monotonic()
        while EVENTS and EVENTS[0][0]<=now-60:
            EVENTS.popleft()
        return dict(counts=dict(COUNTS),minute=dict(Counter(p for _,p in EVENTS)),last=dict(LAST),day=DAY)
