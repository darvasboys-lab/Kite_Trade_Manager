@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo ====================================================
echo   DHAN COVERED CALL - PAYOFF STUDIO (OFFLINE DEMO)
echo ====================================================
echo Folder: %CD%
echo.
if not exist "dhan_visual\app.py" goto missing_app
if not exist "requirements-dhan-visual.txt" goto missing_app
echo [1/3] Checking private Python environment...
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv with Python launcher...
  py -3 -m venv .venv
  if errorlevel 1 goto failed_setup
)
".venv\Scripts\python.exe" --version
if errorlevel 1 goto failed_setup
echo.
echo [2/3] Installing preview packages into this folder only...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-dhan-visual.txt
if errorlevel 1 goto failed_install
echo.
echo [3/3] Starting Streamlit...
echo Dashboard URL: http://127.0.0.1:8503
echo Keep this window OPEN while using the dashboard.
echo Stop with CTRL+C.
echo.
".venv\Scripts\python.exe" -m streamlit run "dhan_visual\app.py" --server.address 127.0.0.1 --server.port 8503
set "RUN_EXIT=%ERRORLEVEL%"
echo.
echo Streamlit has stopped. Exit code: %RUN_EXIT%
if not "%RUN_EXIT%"=="0" (
  echo STARTUP ERROR: See messages above. Run this command manually for diagnosis:
  echo .venv\Scripts\python.exe -m streamlit run dhan_visual\app.py --server.address 127.0.0.1 --server.port 8503
  pause
)
exit /b %RUN_EXIT%
:missing_app
echo ERROR: This folder is missing the preview application or requirements file.
echo Run this script from the DhanPayoffStudio repository folder.
pause
exit /b 1
:failed_setup
echo ERROR: Cannot create or run .venv Python. Check py --version and your antivirus.
pause
exit /b 1
:failed_install
echo ERROR: Python package installation failed. Check the pip messages above.
pause
exit /b 1
