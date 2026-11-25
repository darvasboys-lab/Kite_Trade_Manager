@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CCS_ROOT=%CD%"
set "CCS_ICONS=%LOCALAPPDATA%\CCSTerminal\icons"
if not defined LOCALAPPDATA set "CCS_ICONS=%USERPROFILE%\CCSTerminal\icons"
echo Creating CCS Terminal icons...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "dhan_visual\desktop_icons.py" "%CCS_ICONS%"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 "dhan_visual\desktop_icons.py" "%CCS_ICONS%"
    ) else (
        python "dhan_visual\desktop_icons.py" "%CCS_ICONS%"
    )
)
if errorlevel 1 goto failed
if not exist "%CCS_ICONS%\ccs-terminal-v2.ico" goto failed
if not exist "%CCS_ICONS%\ccs-update-v2.ico" goto failed
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $root=$env:CCS_ROOT; $icons=$env:CCS_ICONS; $desktop=[Environment]::GetFolderPath('Desktop'); $shell=New-Object -ComObject WScript.Shell; foreach($item in @(@('CCS Terminal','Open_CCS.bat','ccs-terminal-v2.ico','Open the read-only CCS Terminal'),@('Update CCS Terminal','Update_CCS.bat','ccs-update-v2.ico','Install CCS GitHub updates'))){$shortcut=$shell.CreateShortcut((Join-Path $desktop ($item[0]+'.lnk')));$shortcut.TargetPath=(Join-Path $root $item[1]);$shortcut.WorkingDirectory=$root;$shortcut.IconLocation=(Join-Path $icons $item[2])+',0';$shortcut.Description=$item[3];$shortcut.Save()}"
if errorlevel 1 goto failed
echo Updated both existing desktop shortcuts with custom CCS icons.
echo If Windows still shows an old icon, refresh the desktop with F5.
pause
exit /b 0
:failed
echo Shortcut setup failed. Check the messages above.
pause
exit /b 1
