@echo off
title MSI Project Library
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Project-Library.ps1"
set "taskLibraryExit=%ERRORLEVEL%"
if not "%taskLibraryExit%"=="0" pause
exit /b %taskLibraryExit%
