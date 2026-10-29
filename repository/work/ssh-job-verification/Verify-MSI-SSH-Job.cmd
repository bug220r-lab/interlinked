@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Connect-To-MSI.ps1" -Mode Verify
set "checkResult=%ERRORLEVEL%"
echo.
echo SSH job check exited with code %checkResult%. Keep the SSH-Verification-Run-* receipts.
pause
exit /b %checkResult%
