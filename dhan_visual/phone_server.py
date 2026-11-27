"""Local CCS phone API. Start with python -m dhan_visual.phone_server.
No order routes. Single local process; stop the desktop CCS before starting.
"""
import json
import secrets
import time
import hmac
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from math import ceil
from dataclasses import asdict

from .strategy import Position, plan, expiry_payoff, exposure_after_fills, current_closeout
from .payoff import current_expiry_payoff, expiry_breakevens
from .mobile_fyers_vault import MobileVault, exists, VaultError
from .fyers_readonly import (authorization_url, new_state, parse_authorization_return,
    exchange_code, FyersDataError, fetch_cash_quotes, fetch_strategy_expiries,
    fetch_strategy_quotes, fetch_strategy_chain, fetch_r1_intraday_bars)
from .fast_universe import load_fast_catalogue
from .screener import expiry_options, expiry_sort_key
from .ce_eod_remote import IST
from .selective_scan import (new_snapshot, _universe_rows, contract_candidates,
    qualified_rows, premium_rows, pending_r1_symbols, load_shortlist_history, run_automatic_batch,
    automatic_work_pending, load_futures_discount)
from .technical_ui import attach_technical_columns
from .r1_today import session_r1_cross
from .smart_suggestions import bullish_checks
from .phone_progress import ScanProgress
from .phone_call_detail import call_detail

PORT = 8502
HOST = f"127.0.0.1:{PORT}"
ORIGIN = "http://" + HOST

def allowed_host(host):
    return host in (HOST, f"localhost:{PORT}")

TOKEN = secrets.token_urlsafe(32)
ROOT = Path(__file__).parent / "phone"
LOCK = Lock()
STATE = {"vault": None, "oauth": None, "universe": None, "scan": None}
LAST_BATCH = 0.0
PROGRESS = ScanProgress()

class PhoneInputError(ValueError):
    """Explicit, safe user-facing error; never include request secrets."""


def access():
    vault = STATE["vault"]
    a = vault.access() if vault else None
    if not a:
        raise PhoneInputError("Unlock and authorize FYERS in Settings first.")
    return a

def clear_scan():
    STATE["scan"] = None
    PROGRESS.reset()
    STATE.pop("verified_months",None)

def catalogue():
    if STATE["universe"] is None:
        STATE["universe"] = load_fast_catalogue()[0]
    return STATE["universe"]

def months():
    today = datetime.now(IST).date()
    # Refresh the public catalogue on date rollover, never pick older months.
    if STATE.get("catalogue_day") != today:
        STATE["universe"] = None
        STATE["catalogue_day"] = today
    try:
        entries = [item.entry for item in catalogue()]
    except Exception:
        raise PhoneInputError("Could not load the NSE F&O catalogue. Check internet and retry Load expiries.") from None
    options = [m for m in expiry_options(entries) if expiry_sort_key(m) >= (today.year,today.month)]
    if not options:
        raise PhoneInputError("No current or future F&O expiry in the verified catalogue.")
    v = STATE["vault"]
    a = v.access() if v else None
    if not a:
        return options
    key = (today,a.app_id,a.expires_at,tuple(options))
    cached = STATE.get("verified_months")
    if cached and cached[0] == key:
        return cached[1]
    # One paced FYERS request to verify that the listed months have not expired.
    probe = next((e for e in entries if e.symbol == "SBIN" and all(m in e.lots for m in options)),None)
    probe = probe or next((e for e in entries if all(m in e.lots for m in options)),None)
    if probe is None:
        raise PhoneInputError("No common stock for verifying the listed expiries. Refresh the catalogue.")
    verified = fetch_strategy_expiries(a,probe.symbol,options)
    options = [m for m in options if m in verified]
    if not options:
        raise PhoneInputError("No unexpired monthly expiry verified by FYERS.")
    STATE["verified_months"] = (key,options)
    return options

def status():
    v = STATE["vault"]
    a = v.access() if v else None
    return {"saved": exists(), "unlocked": v is not None, "connected": a is not None,
            "expires": a.expires_at.astimezone(IST).isoformat() if a else None}

def frame_rows(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))

def report_scan_progress(s, available, stage, *, complete=False):
    symbols = set(contract_candidates(available,s)["Stock"])
    shortlist = set(premium_rows(available,s)["Stock"])
    history_done = len(shortlist & set(s["levels"]))
    options_done = len(symbols & s["attempted"])
    PROGRESS.update(stage=stage,cash_ready=bool(s["cash_at"]),
                    options_done=options_done,options_total=len(symbols),
                    history_done=history_done,history_total=len(shortlist),
                    missing_options=max(0,options_done-len(symbols & set(s["calls"]))),
                    missing_history=sum(any(v is None for v in bullish_checks(
                        getattr(s["cash"].get(symbol),"price",None),s["levels"].get(symbol)).values())
                        for symbol in shortlist),
                    stopped=bool(s["stopped"]),complete=complete and not s["stopped"])


def finish_scan_progress(s,available):
    complete = bool(s["cash_at"]) and not s["stopped"] and not automatic_work_pending(available,s)
    report_scan_progress(s,available,"stopped" if s["stopped"] else "complete" if complete else "waiting",complete=complete)
    if complete and not s.get("completed_at"):
        s["completed_at"] = datetime.now(IST).isoformat()


def scan_result():
    s = STATE["scan"]
    if not s:
        return {"rows": [], "pending": False}
    if s["scan_day"] != datetime.now(IST).date().isoformat():
        clear_scan()
        return {"rows": [], "pending": False, "expired": True}
    available = _universe_rows(catalogue(), s["expiry"])
    frame = qualified_rows(available, s)
    technical = attach_technical_columns(
        frame.rename(columns={"Stock":"Symbol","CMP (₹)":"Last price (₹)"}),
        s["levels"], stage=4).rename(columns={"Symbol":"Stock","Last price (₹)":"CMP (₹)"})
    technical["R1 crossed today"] = technical["Stock"].map(
        lambda symbol: "Yes" if s["crosses"].get(symbol) else
        ("No" if symbol in s["crosses"] else "Unknown"))
    candidates = contract_candidates(available, s)
    rows = frame_rows(technical)
    for row in rows:
        symbol = row["Stock"]
        quote = s["cash"].get(symbol)
        levels = s["levels"].get(symbol)
        row["bullish_checks"] = bullish_checks(getattr(quote,"price",None),levels)
        row["technical_asof"] = getattr(levels,"asof",None)
        row["otm_call_detail"] = call_detail(s["calls"].get(symbol),symbol=symbol,
            expiry=s["expiry"],premium_pct=row.get("5%+ OTM CE (%)"),lot=row.get("Lot size"))
    return {"rows": rows, "expiry": s["expiry"], "scan_id":s.get("scan_id"),
            "completed_at":s.get("completed_at"),"cash_asof":s["cash_at"],"scan_day":s["scan_day"],
            "checked": len(set(candidates["Stock"]) & s["attempted"]),
            "total": len(candidates), "cash": len(s["cash"]),
            "pending": not s["stopped"] and automatic_work_pending(available,s),
            "error": s["error"], "asof": s["options_at"], "filters":{"expiry":s["expiry"],"maximum":s["maximum"],"minimum":s["minimum"],"r1":s["r1_filter"]=="Above R1 only"},
            "retry_after": max(0, ceil(60 - (time.monotonic()-LAST_BATCH))), "progress": PROGRESS.snapshot()}

def strategy(d):
    def dec(k):
        return Decimal(str(d[k]))
    def integer(k):
        v = d[k]
        if type(v) is not int:
            raise PhoneInputError("Share counts and batches must be integers.")
        return v
    p = Position(dec("avg"),integer("shares"),dec("strike"),integer("lot"),
                 dec("credit"),dec("tick"))
    fees = dec("fees")
    pv = plan(p,integer("batches"),fees)
    done = integer("done")
    owned, cost = exposure_after_fills(p,pv,done)
    curve = []
    for i in range(81):
        x = p.strike * Decimal(i+20)/Decimal(80)
        curve.append([float(x),float(current_expiry_payoff(p,pv,done,x,fees)),
                      float(expiry_payoff(p,pv,x,fees))])
    return {"plan":asdict(pv),"owned":owned,"uncovered":p.option_shares-owned,
            "closeout":current_closeout(p,pv,done,dec("spot"),dec("ask"),dec("exit_fees")),
            "breakevens":asdict(expiry_breakevens(p,pv,done,fees)),"curve":curve}

def dispatch(route,d):
    global LAST_BATCH
    if route == "/status":
        return status()
    if route == "/strategy":
        return strategy(d)
    if route == "/unlock":
        STATE["vault"] = MobileVault.unlock(d["passphrase"])
        STATE["oauth"] = None
        clear_scan()
        return status()
    if route == "/setup":
        STATE["vault"] = MobileVault.create(d["passphrase"],d["app_id"],d["secret"])
        STATE["oauth"] = None
        clear_scan()
        return status()
    if route == "/lock":
        STATE["vault"] = STATE["oauth"] = None
        clear_scan()
        return status()
    if route == "/authorize":
        v = STATE["vault"]
        if not v:
            raise PhoneInputError("Unlock the credential vault first.")
        state = new_state()
        STATE["oauth"] = (state,time.monotonic()+600)
        return {"url":authorization_url(v.credentials()[0],state)}
    if route == "/connect":
        pending = STATE["oauth"]
        STATE["oauth"] = None
        v = STATE["vault"]
        if not v or not pending or time.monotonic()>pending[1]:
            raise PhoneInputError("Login expired. Start authorization again.")
        code = parse_authorization_return(d["url"],pending[0])
        app,secret = v.credentials()
        v.save_access(exchange_code(app,secret,code),secret)
        clear_scan()
        return status()
    if route == "/months":
        available = months()
        return {"months":available,"default":available[0]}
    if route == "/scan":
        a = access()
        available_months = months()
        expiry = d.get("expiry") or available_months[0]
        maximum,minimum = d.get("maximum",7),d.get("minimum",2)
        r1_filter = "Above R1 only" if d.get("r1") is True else "Include all stocks"
        if expiry not in available_months:
            raise PhoneInputError("Selected expiry has expired or is unavailable. Reload expiries.")
        if type(maximum) is not int or maximum not in range(4,11) or type(minimum) is not int or minimum not in range(2,9):
            raise PhoneInputError("Choose contract size ₹4–10 lakh and CE premium threshold 2–8%.")
        existing = STATE["scan"]
        if existing and not d.get("refresh") and existing["scan_day"] == datetime.now(IST).date().isoformat() and (existing["expiry"],existing["maximum"],existing["minimum"],existing["r1_filter"]) == (expiry,maximum,minimum,r1_filter):
            return scan_result()
        now = time.monotonic()
        if LAST_BATCH and now-LAST_BATCH < 60:
            return {"rows":[],"pending":False,"cooldown":max(1,ceil(60-(now-LAST_BATCH)))}
        s = new_snapshot("phone",expiry,maximum,minimum,r1_filter)
        s["scan_id"] = secrets.token_hex(16)
        STATE["scan"] = s
        available = _universe_rows(catalogue(),expiry)
        PROGRESS.reset(d.get("progress_id"))
        PROGRESS.update(stage="cash",cash_total=len(available))
        LAST_BATCH = now
        s["scan_day"] = datetime.now(IST).date().isoformat()
        try:
            s["cash"] = fetch_cash_quotes(a,[item.entry.symbol for item in available],
                progress=lambda done,total,received: PROGRESS.update(stage="cash",cash_done=done,cash_total=total,cash_received=received))
            if not s["cash"]:
                raise FyersDataError("FYERS returned no usable stock quotes. Scan stopped; check broker data access.")
            s["cash_at"] = datetime.now(IST).isoformat()
            s["auto_scan"] = True
            report_scan_progress(s,available,"options")
            run_automatic_batch(a,available,s,progress=lambda stage: report_scan_progress(s,available,stage))
        except FyersDataError as exc:
            s["error"],s["stopped"] = str(exc),True
        except Exception as exc:
            print("CCS initial scan failed:",type(exc).__name__,flush=True)
            s["error"],s["stopped"] = "Initial scan failed ("+type(exc).__name__+"). Apply filters to retry manually.",True
        finally:
            LAST_BATCH = time.monotonic()
            finish_scan_progress(s,available)
        return scan_result()
    if route == "/next":
        a = access()
        result = scan_result()
        if not result["pending"]:
            return result
        if result["retry_after"] > 0 or time.monotonic()-LAST_BATCH < 60:
            return result
        s = STATE["scan"]
        LAST_BATCH = time.monotonic()
        s["auto_scan"] = True
        available = _universe_rows(catalogue(),s["expiry"])
        PROGRESS.update(request_id=d.get("progress_id"),stage="options")
        try:
            run_automatic_batch(a,available,s,progress=lambda stage: report_scan_progress(s,available,stage))
        except Exception as exc:
            s["error"],s["stopped"] = "Scan batch failed ("+type(exc).__name__+"). Apply filters to retry manually.",True
        finally:
            LAST_BATCH = time.monotonic()
            finish_scan_progress(s,available)
        return scan_result()
    if route == "/snapshot":
        access()
        return scan_result()
    if route == "/r1":
        a = access()
        result = scan_result()
        s = STATE["scan"]
        if not s or s["stopped"]:
            raise PhoneInputError("No active scan.")
        if not d.get("scan_id") or d["scan_id"] != s.get("scan_id") or result["pending"]:
            raise PhoneInputError("Accept the completed fresh scan before checking intraday R1.")
        requested = d["symbols"]
        if not isinstance(requested,list) or len(requested)>10:
            raise PhoneInputError("Select at most ten symbols.")
        allowed = {row["Stock"] for row in result["rows"]}
        today = datetime.now(IST).date()
        for symbol in dict.fromkeys(requested):
            if symbol not in allowed or symbol not in s["levels"] or symbol in s["crosses"]:
                continue
            level = s["levels"][symbol].monthly_r1
            if level is None:
                continue
            try:
                cross,_ = session_r1_cross(fetch_r1_intraday_bars(a,symbol,day=today),level,today)
                s["crosses"][symbol] = cross
            except FyersDataError as exc:
                s["error"],s["stopped"] = str(exc),True
                break
        return scan_result()
    if route == "/futures":
        a = access()
        if d["expiry"] not in months():
            raise PhoneInputError("Invalid month.")
        f,total,cash = load_futures_discount(a,catalogue(),d["expiry"])
        return {"rows":frame_rows(f),"total":total,"cash":cash,"asof":datetime.now(IST).isoformat()}
    if route == "/chain":
        a = access()
        symbol,expiry = d["symbol"],d["expiry"]
        entry = next((item.entry for item in catalogue() if item.entry.symbol==symbol),None)
        if entry is None or expiry not in entry.lots:
            raise PhoneInputError("Choose a verified stock and month.")
        available = expiry_options([entry])[:3]
        verified = fetch_strategy_expiries(a,symbol,available)
        if expiry not in verified:
            raise PhoneInputError("Selected expiry unavailable at FYERS.")
        quotes = fetch_strategy_quotes(a,symbol,list(verified))
        chain = fetch_strategy_chain(a,symbol,expiry,verified[expiry][0])
        return {"quotes":{k:asdict(v) for k,v in quotes.items()},
                "months":list(verified),"lot":entry.lots[expiry],
                "rows":list(chain.values()),"asof":datetime.now(IST).isoformat()}
    raise PhoneInputError("Unknown API operation.")

class Handler(BaseHTTPRequestHandler):
    timeout = 15
    def log_message(self,*args):
        pass
    def send_bytes(self,code,data,mime):
        self.send_response(code)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)
    def response(self,code,data):
        self.send_bytes(code,json.dumps(data,default=str,allow_nan=False).encode(),"application/json")
    def do_GET(self):
        if not allowed_host(self.headers.get("Host")):
            return self.response(403,{"error":"Invalid host"})
        files = {"/":("index.html","text/html; charset=utf-8"),
                 "/manifest.webmanifest":("manifest.webmanifest","application/manifest+json"),
                 "/icon.svg":("icon.svg","image/svg+xml"),"/sw.js":("sw.js","text/javascript"),
                 "/smart.js":("smart.js","text/javascript")}
        item = files.get(self.path)
        if not item:
            return self.response(404,{"error":"Not found"})
        data = (ROOT/item[0]).read_bytes()
        if self.path=="/":
            data = data.replace(b"__CCS_TOKEN__",TOKEN.encode())
        self.send_bytes(200,data,item[1])
    def do_POST(self):
        host = self.headers.get("Host")
        if (not allowed_host(host) or self.headers.get("Origin")!="http://"+host
                or not hmac.compare_digest(self.headers.get("X-CCS-Token",""),TOKEN)):
            return self.response(403,{"error":"Invalid local request"})
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.response(415,{"error":"JSON required"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=16384:
                raise PhoneInputError("Invalid request size.")
            d=json.loads(self.rfile.read(size))
            if not isinstance(d,dict):
                raise PhoneInputError("Invalid request.")
            if self.path == "/progress":
                # Read only a copy of the published counters, never mutable scan state.
                return self.response(200,PROGRESS.snapshot())
            if not LOCK.acquire(blocking=False):
                return self.response(409,{"error":"Another operation is running. Try again after it finishes."})
            try:
                result=dispatch(self.path,d)
                self.response(200,result)
            finally:
                LOCK.release()
        except (PhoneInputError,FyersDataError,VaultError) as exc:
            self.response(400,{"error":str(exc)})
        except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
            # Do not echo arbitrary exception text or secrets from requests.
            print("CCS request failed:", self.path.split("?")[0], type(exc).__name__, flush=True)
            self.response(400,{"error":"Could not complete this operation ("+type(exc).__name__+"). Check the selected inputs; if it persists, send the terminal diagnostic."})
        except Exception as exc:
            print("CCS request failed:", self.path.split("?")[0], type(exc).__name__, flush=True)
            self.response(500,{"error":"Local operation failed ("+type(exc).__name__+"). No automatic retry."})

if __name__=="__main__":
    print("CCS Phone candidate: http://localhost:"+str(PORT),flush=True)
    print("Legacy local address: "+ORIGIN,flush=True)
    ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
