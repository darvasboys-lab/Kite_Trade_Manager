@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo CCS Terminal - controlled GitHub update
powershell -NoProfile -Command "if(Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue){exit 1};exit 0" >nul 2>nul
if errorlevel 1 goto busy
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto failed
git branch --show-current | findstr /x "main" >nul
if errorlevel 1 goto failed
for /f "delims=" %%L in ('git status --porcelain') do goto dirty
git remote get-url origin | findstr /i /x /c:"https://github.com/darvasboys-lab/Kite_Trade_Manager.git" /c:"https://github.com/darvasboys-lab/Kite_Trade_Manager" /c:"git@github.com:darvasboys-lab/Kite_Trade_Manager.git" >nul
if errorlevel 1 goto failed
git pull --ff-only origin main
if errorlevel 1 goto failed
git log -1 --oneline
echo Update successful. Reopen CCS Terminal.
pause
exit /b 0
:busy
echo Close the CCS server window first. No update performed.
pause
exit /b 1
:dirty
echo Local modifications or untracked files detected. No update performed.
pause
exit /b 1
:failed
echo Update refused or failed. Check Git output above. No reset or forced update was attempted.
pause
exit /b 1
