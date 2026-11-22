@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv >nul 2>nul
  if not exist ".venv\Scripts\python.exe" python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3 was not found. Install Python 3.11 or later and reopen.
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
if not exist ".env" copy ".env.example" ".env" >nul
start "Kite GTT Monitor (keep running)" "%~dp0run_worker.bat"
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
