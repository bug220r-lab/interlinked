@echo off
setlocal
"%~dp0Project-Library-Client\Runtime\python.exe" -I -B -X utf8 "%~dp0INTERLINKED-App\interlinked_gui.py"
set "taskExit=%errorlevel%"
echo.
if not "%taskExit%"=="0" pause
exit /b %taskExit%
