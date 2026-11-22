"""Windows-friendly Kite Trade Manager startup using Python standard library.

If start.bat exits without starting the dashboard, run:
    py -3 launch.py
or:
    python launch.py

This launcher never places broker orders.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".kite_requirements_sha256"


def run(args: list[str], description: str) -> None:
    print(f"[KITE] {description}", flush=True)
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f"{description} failed with exit code {result.returncode}")


def diagnose() -> None:
    print(f"[KITE] Project: {ROOT}", flush=True)
    print(f"[KITE] Python: {sys.executable} ({sys.version.split()[0]})", flush=True)
    print(f"[KITE] Virtual environment exists: {VENV_PYTHON.is_file()}", flush=True)
    print(f"[KITE] Requirements exist: {REQUIREMENTS.is_file()}", flush=True)
    print(f"[KITE] Dashboard file exists: {(ROOT / 'app.py').is_file()}", flush=True)


def main() -> int:
    diagnose()
    if "--diagnose" in sys.argv:
        return 0
    if sys.version_info < (3, 11):
        print("[KITE] ERROR: Python 3.11 or newer is required.", file=sys.stderr)
        return 1
    for path in (REQUIREMENTS, ROOT / "app.py", ROOT / "worker.py", ROOT / ".env.example"):
        if not path.is_file():
            print(f"[KITE] ERROR: Missing project file: {path}", file=sys.stderr)
            return 1
    try:
        if not VENV_PYTHON.is_file():
            run([sys.executable, "-m", "venv", str(VENV)], "Creating private Python environment")
        if not VENV_PYTHON.is_file():
            raise RuntimeError("Virtual environment Python missing after creation")
        fingerprint = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
        if not STAMP.is_file() or STAMP.read_text(encoding="utf-8").strip() != fingerprint:
            run([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS)], "Installing dependencies")
            STAMP.write_text(fingerprint + "\n", encoding="utf-8")
        env_file = ROOT / ".env"
        if not env_file.exists():
            shutil.copyfile(ROOT / ".env.example", env_file)
            print("[KITE] Created local .env. Live GTT execution stays disabled by default.", flush=True)
        if os.name == "nt":
            print("[KITE] Starting monitoring worker window; worker does not create GTTs.", flush=True)
            subprocess.Popen(
                [str(VENV_PYTHON), str(ROOT / "worker.py")],
                cwd=ROOT,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            print("[KITE] Outside Windows, start worker.py separately when monitoring is needed.", flush=True)
        print("[KITE] Dashboard URL: http://127.0.0.1:8501", flush=True)
        run([
            str(VENV_PYTHON), "-m", "streamlit", "run", "app.py",
            "--server.address", "127.0.0.1",
            "--server.port", "8501",
            "--browser.gatherUsageStats", "false",
        ], "Starting Streamlit dashboard")
        return 0
    except (RuntimeError, OSError) as exc:
        print(f"[KITE] ERROR: {exc}", file=sys.stderr, flush=True)
        print("[KITE] Please copy this error for troubleshooting; no broker orders were sent by the launcher.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
