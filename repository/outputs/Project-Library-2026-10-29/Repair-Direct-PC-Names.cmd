@echo off
setlocal
echo This guarded helper adds only the known opposite PC name for the direct Ethernet cable.
echo It requests normal Windows administrator consent and preserves the original hosts file.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Repair-Direct-PC-Names.ps1" -Apply
set "repairResult=%ERRORLEVEL%"
echo.
echo Repair finished with exit code %repairResult%. Zero means verified success; other codes require reviewing the receipt.
echo Backups and JSON receipts are in Connection-Repair-Logs beside this launcher.
echo No firewall, network profile, or client settings were changed by this helper.
pause
exit /b %repairResult%
