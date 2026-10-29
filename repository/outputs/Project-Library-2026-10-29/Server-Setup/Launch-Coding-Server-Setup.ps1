#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Inspect','Apply','Resume','RetryNative','Restore')][string]$Mode='Inspect',
    [string]$ConfirmStateId,
    [switch]$RemoveInstalledCapability
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$helper=Join-Path $PSScriptRoot 'Set-Up-Coding-Server.ps1'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Reviewed setup helper is missing.' }
if ($Mode -in @('Resume','RetryNative','Restore') -and $ConfirmStateId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Recovery/Restore requires the recorded StateId.' }
if ($RemoveInstalledCapability -and $Mode -ne 'Restore') { throw 'Capability removal is available only during guarded Restore.' }
$logs=Join-Path $PSScriptRoot 'Logs'
if (-not (Test-Path -LiteralPath $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }
$log=Join-Path $logs ('LAUNCH-'+[guid]::NewGuid().ToString('N')+'.json')
$binary=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
# All argument values are a verified local helper path or validated fixed options.
$arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$helper+'" -Mode '+$Mode
if ($Mode -in @('Resume','RetryNative','Restore')) { $arguments+=' -ConfirmStateId '+$ConfirmStateId }
if ($RemoveInstalledCapability) { $arguments+=' -RemoveInstalledCapability' }
$result=[ordered]@{Mode=$Mode;Helper=$helper;RequestedUtc=[DateTime]::UtcNow.ToString('o');Status='LAUNCH_REQUESTED';ProcessId=$null;Error=$null;ResultLogs=$logs}
try {
    $process=Start-Process -FilePath $binary -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
    $result.Status='ELEVATED_PROCESS_STARTED';$result.ProcessId=$process.Id
} catch { $result.Status='LAUNCH_FAILED_OR_UAC_CANCELLED';$result.Error=$_.Exception.Message }
[IO.File]::WriteAllText($log,($result | ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$result | ConvertTo-Json -Depth 5
if ($result.Status -ne 'ELEVATED_PROCESS_STARTED') { exit 1 }
