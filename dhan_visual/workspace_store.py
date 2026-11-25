"""Versioned local non-secret preferences, snapshots and hypothetical strategies."""
from pathlib import Path
from datetime import datetime
import json
import os
import tempfile
from threading import RLock
import math
import re

VERSION = "1.0.0"
LOCK = RLock()
MAX_BYTES = 4_000_000
DEFAULT = {"version": 1, "remember": False, "preferences": {}, "presets": {},
           "strategies": {}, "snapshot": None}
MONTH = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-[0-9]{2}$")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,39}$")


def location():
    return Path(os.environ.get("CCS_DATA_DIR") or
                (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CCSTerminal")) / "workspace.json"


def filters(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid filters.")
    maximum, minimum, expiry, r1 = (data.get(k) for k in ("maximum","minimum","expiry","r1_filter"))
    if (type(maximum) is not int or maximum not in range(4,11)
            or type(minimum) is not int or minimum not in range(2,9)
            or not isinstance(expiry,str) or not MONTH.fullmatch(expiry)
            or r1 not in ("Include all stocks","Above R1 only")):
        raise ValueError("Invalid scan filters.")
    return dict(maximum=maximum,minimum=minimum,expiry=expiry,r1_filter=r1)


def validate(raw):
    from .stock_strategy import Leg
    if not isinstance(raw,dict) or raw.get("version") != 1:
        raise ValueError("Unsupported workspace backup.")
    out = json.loads(json.dumps(DEFAULT))
    out["remember"] = raw.get("remember") is True
    prefs = raw.get("preferences",{})
    if not isinstance(prefs,dict):
        raise ValueError("Invalid preferences.")
    if "filters" in prefs:
        out["preferences"]["filters"] = filters(prefs["filters"])
    for key in ("dark","autofit"):
        if type(prefs.get(key)) is bool:
            out["preferences"][key] = prefs[key]
    for collection in ("presets","strategies"):
        source = raw.get(collection,{})
        if not isinstance(source,dict) or len(source)>100:
            raise ValueError("Too many saved items.")
        for name,value in source.items():
            if not isinstance(name,str) or not 1<=len(name.strip())<=60:
                raise ValueError("Invalid saved name.")
            if collection=="presets":
                out[collection][name] = filters(value)
                continue
            if not isinstance(value,dict) or not isinstance(value.get("symbol"),str) or not SYMBOL.fullmatch(value["symbol"]):
                raise ValueError("Invalid strategy symbol.")
            expiry=value.get("expiry")
            if not isinstance(expiry,str) or not MONTH.fullmatch(expiry):
                raise ValueError("Invalid strategy expiry.")
            legs=value.get("legs")
            if not isinstance(legs,list) or len(legs)>30:
                raise ValueError("Invalid strategy legs.")
            from dataclasses import asdict
            checked=[]
            for row in legs:
                if not isinstance(row,dict) or set(row)!={"kind","side","expiry","strike","units","price"}:
                    raise ValueError("Invalid leg fields.")
                leg=Leg(**row)
                if leg.units>100_000_000 or (leg.kind!="Equity" and not MONTH.fullmatch(leg.expiry)):
                    raise ValueError("Invalid leg size or expiry.")
                checked.append(asdict(leg))
            charges=value.get("charges",0)
            if type(charges) not in (float,int) or not math.isfinite(charges) or not 0<=charges<=1e9:
                raise ValueError("Invalid charges.")
            out[collection][name]=dict(symbol=value["symbol"],expiry=expiry,legs=checked,charges=charges)
    snap=raw.get("snapshot")
    if snap is not None:
        if not isinstance(snap,dict) or not isinstance(snap.get("rows"),list) or len(snap["rows"])>1000:
            raise ValueError("Invalid cached shortlist.")
        stamp=snap.get("saved_at")
        if not isinstance(stamp,str) or len(stamp)>80:
            raise ValueError("Invalid snapshot date.")
        datetime.fromisoformat(stamp)
        columns={"Stock","CMP (₹)","Lot size","Contract size (₹)","5%+ OTM CE (%)","ATM Straddle Total (₹)","Straddle (%)","> R1"}
        rows=[]
        for row in snap["rows"]:
            if not isinstance(row,dict) or not isinstance(row.get("Stock"),str) or not SYMBOL.fullmatch(row["Stock"]):
                raise ValueError("Invalid cached stock.")
            clean={}
            for k,v in row.items():
                if k not in columns:
                    continue
                if k=="Stock":
                    clean[k]=v
                elif k=="> R1":
                    if v not in ("✅ Yes","No","—"):
                        raise ValueError("Invalid cached R1.")
                    clean[k]=v
                elif v is None or (type(v) in (int,float) and math.isfinite(v) and v>=0):
                    clean[k]=v
                else:
                    raise ValueError("Invalid cached quote.")
            rows.append(clean)
        out["snapshot"]=dict(saved_at=stamp,filters=filters(snap["filters"]),rows=rows)
    return out


def read():
    with LOCK:
        path=location()
        if not path.exists():
            return json.loads(json.dumps(DEFAULT))
        if path.stat().st_size>MAX_BYTES:
            raise ValueError("Workspace file too large.")
        return validate(json.loads(path.read_text(encoding="utf-8")))


def write(data):
    with LOCK:
        data=validate(data)
        payload=json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)
        if len(payload.encode("utf-8"))>MAX_BYTES:
            raise ValueError("Workspace exceeds size limit.")
        path=location()
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix=".ccs-",dir=path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def change(fn):
    with LOCK:
        data=read()
        fn(data)
        write(data)
        return data
