@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8502/_stcore/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0} }catch{}; exit 1" >nul 2>nul
if not errorlevel 1 (
    start "" "http://127.0.0.1:8502"
    exit /b 0
)
start "CCS Terminal" cmd /k ""%~dp0launch_dhan.bat""
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8502/_stcore/health' -TimeoutSec 2;if($r.StatusCode -eq 200){Start-Process 'http://127.0.0.1:8502';exit 0}}catch{};Start-Sleep -Seconds 1};exit 1"
if errorlevel 1 (
    echo CCS did not respond yet. Check the server window for errors.
    pause
)
