"""Local worker polls order state only. It never stages new GTTs itself."""
import time
from filelock import FileLock,Timeout
from store import ROOT, log, read
from manager import monitor

def main():
    lock=FileLock(str(ROOT/'worker_instance.lock'),timeout=0)
    try:
        with lock:
            log('Monitor worker online. No live orders will be placed by background worker')
            while True:
                try:
                    if (read('runtime.json',{}) or {}).get('status')=='ACTIVE':
                        monitor()
                except KeyboardInterrupt:
                    break
                except Exception:
                    # Leave GTTs flagged as potentially active; never silent success.
                    log('Worker loop failed unexpectedly. Inspect Kite GTTs immediately','ERROR')
                time.sleep(10)
    except Timeout:
        print('Another worker is already running')
if __name__=='__main__':
    main()
