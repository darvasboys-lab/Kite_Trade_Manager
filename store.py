"""Crash-durable local store; never write broker secrets into activity log."""
import json, os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from filelock import FileLock

ROOT=Path(__file__).resolve().parent
DEFAULTS={
    'stock_symbol':'RELIANCE','option_symbol':'','expiry':'',
    'strike':1400.,'base_price':1300.,'step':20.,'ladder_mode':'AUTO_HALF_PERCENT','initial_shares':100,
    'batch_qty':100,'premium_received_total':0.0,'max_shares':500,'down_price':1280.,'down_sell_qty':100,
    'tick_size':0.05,'limit_buffer_pct':0.5,'mode':'PAPER'
}

def read(file,default=None):
    path=ROOT/file
    with FileLock(str(path)+'.lock',timeout=5):
        if not path.exists():
            return default.copy() if isinstance(default,dict) else default
        return json.loads(path.read_text(encoding='utf-8'))

def write(file,body):
    path=ROOT/file
    with FileLock(str(path)+'.lock',timeout=5):
        target=path.with_name(path.name+'.tmp')
        with target.open('w',encoding='utf-8') as f:
            json.dump(body,f,indent=2,ensure_ascii=False,default=str)
            f.flush(); os.fsync(f.fileno())
        os.replace(target,path)

def settings():
    return {**DEFAULTS,**(read('settings.json',{}) or {})}

def log(msg,kind='INFO'):
    path=ROOT/'events.log'
    with FileLock(str(path)+'.lock',timeout=5):
        with path.open('a',encoding='utf-8') as f:
            f.write(f'{datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")} [{kind}] {msg}\n')
