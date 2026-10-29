@echo off
setlocal
if /i not "%COMPUTERNAME%"=="THIS-MACHINE" (
  echo Run this on the new PC named THIS-MACHINE.
  pause
  exit /b 2
)
echo Connect to the MSI project library as MSI\jlewi.
echo Enter the MSI Windows account password at the native Windows prompt below.
echo Typing the password may show no characters. A Windows Hello PIN will not work.
echo This helper does not read, print, log, or save your password.
echo.
"%SystemRoot%\System32\net.exe" use "\\MSI\ProjectLibrary" /user:"MSI\jlewi" *
set "signinResult=%ERRORLEVEL%"
echo.
if "%signinResult%"=="0" (
  echo Connected. Run Check-MSI-Connection.cmd again, then use Project-Library-Client.
) else (
  echo Windows could not connect. Report the system error number, never your password.
  echo No existing connections or saved credentials were removed by this helper.
)
pause
exit /b %signinResult%
