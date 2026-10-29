@echo off
setlocal
if /i not "%COMPUTERNAME%"=="THIS-MACHINE" (
  echo Run this locally on the new PC named THIS-MACHINE.
  pause
  exit /b 2
)
echo This checks five tiny synthetic files, using a separate test cache.
echo It will save a test version, release it, refetch it, verify it and release it again.
echo Your projects, normal cache, settings and credentials are not changed.
set "PYTHONUTF8=1"
"%~dp0Project-Library-Client\Runtime\python.exe" -B -X utf8 "%~dp0verify_new_pc_workflow.py"
set "verifyResult=%ERRORLEVEL%"
echo.
echo Verification finished with exit code %verifyResult%.
echo Keep the Verification-Run-* receipt folder beside this launcher.
echo If incomplete, stop and report the result; do not delete or reset any project state.
pause
exit /b %verifyResult%
