@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CCS_ROOT=%CD%"
powershell -NoProfile -Command "$root=$env:CCS_ROOT; $desktop=[Environment]::GetFolderPath('Desktop'); $shell=New-Object -ComObject WScript.Shell; foreach($item in @(@('CCS Terminal','Open_CCS.bat'),@('Update CCS Terminal','Update_CCS.bat'))){$shortcut=$shell.CreateShortcut((Join-Path $desktop ($item[0]+'.lnk')));$shortcut.TargetPath=(Join-Path $root $item[1]);$shortcut.WorkingDirectory=$root;$shortcut.Save()}"
if errorlevel 1 (
    echo Shortcut setup failed.
    pause
    exit /b 1
)
echo Created CCS Terminal and Update CCS Terminal desktop shortcuts.
pause
