@echo off
setlocal
cd /d "%~dp0"
if not exist ".git" (
  echo Not a Git checkout. Clone the repository first.
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" update_preflight.py
) else (
  py -3 update_preflight.py
)
if errorlevel 1 (pause & exit /b 1)
git diff --quiet
if errorlevel 1 (echo Local source edits exist. Update cancelled. & pause & exit /b 1)
git diff --cached --quiet
if errorlevel 1 (echo Staged local source edits exist. Update cancelled. & pause & exit /b 1)
git pull --ff-only origin main
if errorlevel 1 (echo Update failed. No forced reset attempted. & pause & exit /b 1)
echo Update complete. Restart start.bat.
pause
