@echo off
setlocal
cd /d "%~dp0"
echo [KITE] Starting Kite Trade Manager in %CD%
where py >nul 2>nul
if not errorlevel 1 (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    py -3 launch.py
    goto done
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python --version >nul 2>nul
  if not errorlevel 1 (
    python launch.py
    goto done
  )
)
echo [KITE] ERROR: Python 3 was not found. Install Python 3.11 or newer.
echo [KITE] You can check with: where python
exit /b 1
:done
if errorlevel 1 echo [KITE] Startup failed. Read the Python error displayed above.
pause
