#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{32}$')][string]$JobId)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if ($env:COMPUTERNAME -ine 'MSI' -or $identity.User.Value -ne 'S-1-5-21-3086848077-1833151167-136462239-1001') { throw 'Run only as the existing MSI\jlewi user.' }
$python='C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$runner=Join-Path $PSScriptRoot 'msi_job_runner.py'
& $python -B $runner --run $JobId
exit $LASTEXITCODE
