@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Connect-To-MSI.ps1" -Mode Terminal
set "terminalResult=%ERRORLEVEL%"
echo.
echo MSI terminal exited with code %terminalResult%.
pause
exit /b %terminalResult%
