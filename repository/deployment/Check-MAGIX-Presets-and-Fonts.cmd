@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-MAGIX-Inventory.ps1"
set "scanResult=%ERRORLEVEL%"
echo.
if not "%scanResult%"=="0" echo Inventory did not finish. Keep this window's error for review.
pause
exit /b %scanResult%
