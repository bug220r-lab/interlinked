@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Check-MSI-Connection.ps1"
set "checkResult=%ERRORLEVEL%"
echo.
echo Connection check finished with exit code %checkResult%.
echo 0 = file workflow and SSH ports reachable; 1 = file workflow works, SSH port check incomplete; 2 = blocked or incomplete.
echo This does not verify an SSH sign-in. The JSON report is beside this launcher.
pause
exit /b %checkResult%
