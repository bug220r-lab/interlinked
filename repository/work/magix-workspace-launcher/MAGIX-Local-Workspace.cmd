@echo off
setlocal
"%~dp0Project-Library-Client\Runtime\python.exe" -I -B -X utf8 "%~dp0magix_workspace.py" menu
set "taskExit=%errorlevel%"
echo.
pause
exit /b %taskExit%
