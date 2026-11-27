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
from dataclasses import asdict

from .strategy import Position, plan, expiry_payoff, exposure_after_fills, current_closeout
from .payoff import current_expiry_payoff, expiry_breakevens
from .mobile_fyers_vault import MobileVault, exists, VaultError
from .fyers_readonly import (authorization_url, new_state, parse_authorization_return,
    exchange_code, FyersDataError, fetch_cash_quotes, fetch_strategy_expiries,
    fetch_strategy_quotes, fetch_strategy_chain, fetch_r1_intraday_bars)
from .fast_universe import load_fast_catalogue
from .screener import expiry_options
from .ce_eod_remote import IST
from .selective_scan import (new_snapshot, _universe_rows, contract_candidates,
    qualified_rows, pending_r1_symbols, load_shortlist_history, run_automatic_batch,
    automatic_work_pending, load_futures_discount)
from .technical_ui import attach_technical_columns
from .r1_today import session_r1_cross

PORT = 8502
HOST = f"127.0.0.1:{PORT}"
ORIGIN = "http://" + HOST
TOKEN = secrets.token_urlsafe(32)
ROOT = Path(__file__).parent / "phone"
LOCK = Lock()
STATE = {"vault": None, "oauth": None, "universe": None, "scan": None}
LAST_BATCH = 0.0

def access():
    vault = STATE["vault"]
    a = vault.access() if vault else None
    if not a:
        raise ValueError("Unlock and authorize FYERS in Settings first.")
    return a

def clear_scan():
    STATE["scan"] = None

def catalogue():
    if STATE["universe"] is None:
        STATE["universe"] = load_fast_catalogue()[0]
    return STATE["universe"]

def months():
    return expiry_options([item.entry for item in catalogue()])

def status():
    v = STATE["vault"]
    a = v.access() if v else None
    return {"saved": exists(), "unlocked": v is not None, "connected": a is not None,
            "expires": a.expires_at.astimezone(IST).isoformat() if a else None}

def frame_rows(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))

def scan_result():
    s = STATE["scan"]
    if not s:
        return {"rows": [], "pending": False}
    if s["scan_day"] != datetime.now(IST).date().isoformat():
        clear_scan()
        raise ValueError("Scan is from a previous session date. Start a fresh scan.")
    available = _universe_rows(catalogue(), s["expiry"])
    frame = qualified_rows(available, s)
    technical = attach_technical_columns(
        frame.rename(columns={"Stock":"Symbol","CMP (₹)":"Last price (₹)"}),
        s["levels"], stage=4).rename(columns={"Symbol":"Stock","Last price (₹)":"CMP (₹)"})
    technical["R1 crossed today"] = technical["Stock"].map(
        lambda symbol: "Yes" if s["crosses"].get(symbol) else
        ("No" if symbol in s["crosses"] else "Unknown"))
    candidates = contract_candidates(available, s)
    return {"rows": frame_rows(technical), "expiry": s["expiry"],
            "checked": len(set(candidates["Stock"]) & s["attempted"]),
            "total": len(candidates), "cash": len(s["cash"]),
            "pending": not s["stopped"] and automatic_work_pending(available,s),
            "error": s["error"], "asof": s["options_at"],
            "retry_after": max(0, round(60 - (time.monotonic()-LAST_BATCH)))}

def strategy(d):
    def dec(k):
        return Decimal(str(d[k]))
    def integer(k):
        v = d[k]
        if type(v) is not int:
            raise ValueError("Share counts and batches must be integers.")
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
            raise ValueError("Unlock the credential vault first.")
        state = new_state()
        STATE["oauth"] = (state,time.monotonic()+600)
        return {"url":authorization_url(v.credentials()[0],state)}
    if route == "/connect":
        pending = STATE["oauth"]
        STATE["oauth"] = None
        v = STATE["vault"]
        if not v or not pending or time.monotonic()>pending[1]:
            raise ValueError("Login expired. Start authorization again.")
        code = parse_authorization_return(d["url"],pending[0])
        app,secret = v.credentials()
        v.save_access(exchange_code(app,secret,code),secret)
        clear_scan()
        return status()
    if route == "/months":
        return {"months":months()}
    if route == "/scan":
        a = access()
        now = time.monotonic()
        if now-LAST_BATCH < 60 and LAST_BATCH:
            raise ValueError("Wait 60 seconds between scan batches, including new scans.")
        expiry = d["expiry"]
        maximum,minimum = d["maximum"],d["minimum"]
        if expiry not in months() or type(maximum) is not int or maximum not in range(4,11) or type(minimum) is not int or minimum not in range(2,9):
            raise ValueError("Invalid scan filters.")
        s = new_snapshot("phone",expiry,maximum,minimum,
                         "Above R1 only" if d.get("r1") is True else "Include all stocks")
        STATE["scan"] = s
        available = _universe_rows(catalogue(),expiry)
        LAST_BATCH = now
        s["scan_day"] = datetime.now(IST).date().isoformat()
        try:
            s["cash"] = fetch_cash_quotes(a,[item.entry.symbol for item in available])
            s["cash_at"] = datetime.now(IST).isoformat()
            s["auto_scan"] = True
            run_automatic_batch(a,available,s)
        except FyersDataError as exc:
            s["error"],s["stopped"] = str(exc),True
        return scan_result()
    if route == "/next":
        a = access()
        result = scan_result()
        if not result["pending"]:
            return result
        if result["retry_after"] > 0 or time.monotonic()-LAST_BATCH < 60:
            raise ValueError("Wait until the next 60-second scan slot.")
        s = STATE["scan"]
        LAST_BATCH = time.monotonic()
        s["auto_scan"] = True
        run_automatic_batch(a,_universe_rows(catalogue(),s["expiry"]),s)
        return scan_result()
    if route == "/snapshot":
        access()
        return scan_result()
    if route == "/r1":
        a = access()
        result = scan_result()
        s = STATE["scan"]
        if not s or s["stopped"]:
            raise ValueError("No active scan.")
        requested = d["symbols"]
        if not isinstance(requested,list) or len(requested)>10:
            raise ValueError("Select at most ten symbols.")
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
            raise ValueError("Invalid month.")
        f,total,cash = load_futures_discount(a,catalogue(),d["expiry"])
        return {"rows":frame_rows(f),"total":total,"cash":cash,"asof":datetime.now(IST).isoformat()}
    if route == "/chain":
        a = access()
        symbol,expiry = d["symbol"],d["expiry"]
        entry = next((item.entry for item in catalogue() if item.entry.symbol==symbol),None)
        if entry is None or expiry not in entry.lots:
            raise ValueError("Choose a verified stock and month.")
        available = expiry_options([entry])[:3]
        verified = fetch_strategy_expiries(a,symbol,available)
        if expiry not in verified:
            raise ValueError("Selected expiry unavailable at FYERS.")
        quotes = fetch_strategy_quotes(a,symbol,list(verified))
        chain = fetch_strategy_chain(a,symbol,expiry,verified[expiry][0])
        return {"quotes":{k:asdict(v) for k,v in quotes.items()},
                "months":list(verified),"lot":entry.lots[expiry],
                "rows":list(chain.values()),"asof":datetime.now(IST).isoformat()}
    raise ValueError("Unknown API operation.")

class Handler(BaseHTTPRequestHandler):
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
        if self.headers.get("Host") != HOST:
            return self.response(403,{"error":"Invalid host"})
        files = {"/":("index.html","text/html; charset=utf-8"),
                 "/manifest.webmanifest":("manifest.webmanifest","application/manifest+json"),
                 "/icon.svg":("icon.svg","image/svg+xml"),"/sw.js":("sw.js","text/javascript")}
        item = files.get(self.path)
        if not item:
            return self.response(404,{"error":"Not found"})
        data = (ROOT/item[0]).read_bytes()
        if self.path=="/":
            data = data.replace(b"__CCS_TOKEN__",TOKEN.encode())
        self.send_bytes(200,data,item[1])
    def do_POST(self):
        if (self.headers.get("Host")!=HOST or self.headers.get("Origin")!=ORIGIN
                or not hmac.compare_digest(self.headers.get("X-CCS-Token",""),TOKEN)):
            return self.response(403,{"error":"Invalid local request"})
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.response(415,{"error":"JSON required"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=16384:
                raise ValueError("Invalid request size.")
            d=json.loads(self.rfile.read(size))
            if not isinstance(d,dict):
                raise ValueError("Invalid request.")
            if not LOCK.acquire(blocking=False):
                return self.response(409,{"error":"Another operation is running. Try again after it finishes."})
            try:
                result=dispatch(self.path,d)
                self.response(200,result)
            finally:
                LOCK.release()
        except (FyersDataError,VaultError) as exc:
            self.response(400,{"error":str(exc)})
        except (ValueError,KeyError,TypeError,ArithmeticError):
            self.response(400,{"error":"Invalid input, expired scan or request not ready. Check fields, connection and batch timing."})
        except Exception:
            self.response(500,{"error":"Operation failed. No automatic retry."})

if __name__=="__main__":
    print("CCS Phone candidate: "+ORIGIN,flush=True)
    ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
