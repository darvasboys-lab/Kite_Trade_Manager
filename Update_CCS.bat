@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
echo CCS Terminal - controlled GitHub update
where git >nul 2>nul
if errorlevel 1 goto nogit
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { if(Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue){exit 1}; exit 0 } catch {exit 2}" >nul 2>nul
if errorlevel 2 goto porterror
if errorlevel 1 goto busy
git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto notrepo
set "CCS_BRANCH="
for /f "delims=" %%B in ('git branch --show-current') do set "CCS_BRANCH=%%B"
if not "%CCS_BRANCH%"=="main" goto wrongbranch
set "CCS_REMOTE="
for /f "delims=" %%R in ('git remote get-url origin') do set "CCS_REMOTE=%%R"
if /i "%CCS_REMOTE%"=="https://github.com/darvasboys-lab/Kite_Trade_Manager.git" goto checkdirty
if /i "%CCS_REMOTE%"=="https://github.com/darvasboys-lab/Kite_Trade_Manager" goto checkdirty
if /i "%CCS_REMOTE%"=="git@github.com:darvasboys-lab/Kite_Trade_Manager.git" goto checkdirty
goto wrongremote
:checkdirty
rem Capture the status and its exit code before checking for any output.
set "CCS_STATUS=%TEMP%\ccs-status-%RANDOM%-%RANDOM%.txt"
git status --porcelain > "%CCS_STATUS%"
if errorlevel 1 goto statuserror
for %%F in ("%CCS_STATUS%") do if %%~zF GTR 0 goto dirty
del "%CCS_STATUS%" >nul 2>nul
echo Preflight passed. Fetching latest approved main...
git pull --ff-only origin main
if errorlevel 1 goto pullfailed
git --no-pager log -1 --oneline
echo Update successful. Reopen CCS Terminal.
pause
exit /b 0
:dirty
echo Local modifications or untracked files detected:
type "%CCS_STATUS%"
del "%CCS_STATUS%" >nul 2>nul
echo No update performed. Preserve your changes before updating.
goto failed
:statuserror
del "%CCS_STATUS%" >nul 2>nul
echo Unable to read Git working-tree status.
goto failed
:nogit
echo Git is not available on PATH.
goto failed
:porterror
echo Unable to check whether CCS is running.
goto failed
:busy
echo Close the CCS server window first. No update performed.
goto failed
:notrepo
echo The shortcut is not pointing to a valid Git checkout.
goto failed
:wrongbranch
echo The checkout is not on main.
goto failed
:wrongremote
echo The origin repository is not the approved CCS repository.
goto failed
:pullfailed
echo Git fast-forward update failed. See Git output above.
:failed
echo No reset, forced update or automatic stash was attempted.
pause
exit /b 1
