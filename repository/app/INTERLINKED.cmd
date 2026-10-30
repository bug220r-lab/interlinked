@echo off
setlocal
if not exist "%~dp0INTERLINKED-Desktop\INTERLINKED.exe" (
  echo INTERLINKED desktop application is missing. Use the reviewed installer.
  pause
  exit /b 2
)
start "" "%~dp0INTERLINKED-Desktop\INTERLINKED.exe"
exit /b %errorlevel%
