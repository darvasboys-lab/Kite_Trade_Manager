@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 (echo Python venv creation failed. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements-dhan-visual.txt
if errorlevel 1 (echo Python package installation failed. & pause & exit /b 1)
".venv\Scripts\python.exe" -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503
if errorlevel 1 pause
