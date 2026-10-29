@echo off
setlocal
echo Connect to MSI as jlewi. Enter the MSI account password only at the SSH prompt.
echo On the first connection, compare the host fingerprint with MSI-SERVER-HANDOFF.txt.
echo This starts a shell on MSI; work only in its managed project working folder.
where ssh.exe >nul 2>nul
if errorlevel 1 (
  echo Windows OpenSSH Client is not available on this PC.
  pause
  exit /b 1
)
ssh.exe -t jlewi@MSI powershell.exe -NoLogo -NoProfile
echo.
pause
