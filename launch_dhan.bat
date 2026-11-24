@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dhan Visual - EMA and F&O Screener

echo [DHAN] Launching read-only EMA and F&O screener...
if not exist "dhan_visual\app.py" (
    echo [DHAN] ERROR: dhan_visual\app.py not found in %CD%
    goto failed
)

set "APP_PY=.venv\Scripts\python.exe"
if not exist "%APP_PY%" (
    echo [DHAN] Local Python environment not found. Creating .venv...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv ".venv"
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo [DHAN] ERROR: Python 3.11 or newer is required.
            goto failed
        )
        python -m venv ".venv"
    )
    if errorlevel 1 goto failed
)
if not exist "%APP_PY%" goto failed

"%APP_PY%" -c "import streamlit, pandas, plotly, yfinance, requests" >nul 2>nul
if errorlevel 1 (
    echo [DHAN] Installing missing read-only screener dependencies...
    "%APP_PY%" -m pip install "streamlit>=1.43,<2" "pandas>=2,<3" "plotly>=5,<7" "yfinance>=0.2,<1" "requests>=2,<3"
    if errorlevel 1 goto failed
)

echo [DHAN] Open http://127.0.0.1:8502 in your browser.
echo [DHAN] Press Ctrl+C here to stop the screener.
"%APP_PY%" -m streamlit run "dhan_visual\app.py" --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
if errorlevel 1 goto failed
exit /b 0

:failed
echo [DHAN] Startup failed. Read the error above; no order was sent.
pause
exit /b 1
