@echo off
setlocal
"%~dp0Project-Library-Client\Runtime\python.exe" -I -B -X utf8 "%~dp0magix_workspace.py" setup --confirm ADOPT
set "taskExit=%errorlevel%"
echo.
if "%taskExit%"=="0" (echo Setup is ready. Next open MAGIX-Local-Workspace.cmd.) else (echo Setup did not finish. Keep its receipts; no project files were released.)
pause
exit /b %taskExit%
