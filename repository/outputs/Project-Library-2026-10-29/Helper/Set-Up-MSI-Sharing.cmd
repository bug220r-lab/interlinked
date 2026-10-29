@echo off
setlocal
set "MSI_LIBRARY_SETUP=%~dp0Set-Up-MSI-Sharing.ps1"
set "MSI_LIBRARY_RESTORE="
if /I "%~1"=="restore" set "MSI_LIBRARY_RESTORE=-Restore"
if not "%~1"=="" if /I not "%~1"=="restore" (
  echo Usage: Set-Up-MSI-Sharing.cmd [restore]
  exit /b 2
)
echo Windows will ask to run the MSI sharing helper as administrator.
echo It must use the existing MSI\jlewi account.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$a=@('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',([char]34+$env:MSI_LIBRARY_SETUP+[char]34),'-PauseWhenDone'); if($env:MSI_LIBRARY_RESTORE){$a+=$env:MSI_LIBRARY_RESTORE}; try{$p=Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $a -Wait -PassThru; exit $p.ExitCode}catch{Write-Host $_.Exception.Message; exit 1}"
exit /b %errorlevel%
