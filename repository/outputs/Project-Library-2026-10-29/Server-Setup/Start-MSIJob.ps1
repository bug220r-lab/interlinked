#requires -Version 5.1
<# Only -Mode Start registers and starts a unique, one-shot owned task.
No periodic trigger, account creation, credential storage, elevation or network call.
The account must already have an interactive Windows logon on MSI. #>
[CmdletBinding()]
param(
    [ValidateSet('Status','Start')][string]$Mode='Status',
    [string]$SpecFile,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$JobId
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$ownerSid='S-1-5-21-3086848077-1833151167-136462239-1001'
if ($env:COMPUTERNAME -ine 'MSI' -or $identity.User.Value -ne $ownerSid) { throw 'Run only as the existing MSI\jlewi account.' }
$python='C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$runner=Join-Path $PSScriptRoot 'msi_job_runner.py'
$taskRunner=Join-Path $PSScriptRoot 'Invoke-MSIJob.ps1'
if ($Mode -eq 'Status') {
    if (-not $JobId) { throw 'Status requires -JobId from the start receipt.' }
    $raw=& $python -B $runner --status $JobId
    if ($LASTEXITCODE -ne 0) { throw 'Job ownership/status validation failed.' }
    $result=$raw | ConvertFrom-Json
    $task=Get-ScheduledTask -TaskPath '\' -TaskName $result.taskName -ErrorAction SilentlyContinue
    $taskInfo=if ($task) { Get-ScheduledTaskInfo -TaskPath '\' -TaskName $result.taskName } else { $null }
    [ordered]@{Job=$result;TaskState=if($task){[string]$task.State}else{'NOT_REGISTERED'};LastTaskResult=if($taskInfo){$taskInfo.LastTaskResult}else{$null};Note='If the runner receipt says RUNNING but the task is no longer Running, investigate before save/release; completion is not inferred.'} | ConvertTo-Json -Depth 10
    return
}
if (-not $SpecFile) { throw 'Start requires a JSON job specification inside the fetched project Files folder.' }
$raw=& $python -B $runner --prepare $SpecFile
if ($LASTEXITCODE -ne 0) { throw 'Job preparation failed; no task was registered.' }
$prepared=$raw | ConvertFrom-Json
if ($prepared.jobId -notmatch '^[0-9a-f]{32}$' -or $prepared.taskName -cne ('MSICodingJob-'+$prepared.jobId)) { throw 'Unexpected prepared job identity.' }
if (Get-ScheduledTask -TaskPath '\' -TaskName $prepared.taskName -ErrorAction SilentlyContinue) { throw 'Task name already exists; never overwriting it.' }
$powerShell=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskRunner+'" -JobId '+$prepared.jobId
$action=New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $prepared.jobDirectory
$principal=New-ScheduledTaskPrincipal -UserId $ownerSid -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$description='Owned one-shot MSI coding job '+$prepared.jobId+'; project '+$prepared.projectId+'; no saved credentials or periodic trigger.'
Register-ScheduledTask -TaskPath '\' -TaskName $prepared.taskName -Action $action -Principal $principal -Settings $settings -Description $description | Out-Null
$receipt=[ordered]@{Status='REGISTERED_NOT_STARTED';JobId=$prepared.jobId;ProjectId=$prepared.projectId;TaskName=$prepared.taskName;TaskPath='\';OwnerSid=$ownerSid;LogonType='Interactive';RunLevel='Limited';RegisteredUtc=[DateTime]::UtcNow.ToString('o');JobDirectory=$prepared.jobDirectory}
$receiptPath=Join-Path $prepared.jobDirectory 'task-registration.json'
[IO.File]::WriteAllText($receiptPath,($receipt | ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
Start-ScheduledTask -TaskPath '\' -TaskName $prepared.taskName
$receipt.Status='START_REQUESTED_NOT_COMPLETED'
[IO.File]::WriteAllText($receiptPath,($receipt | ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
$receipt | ConvertTo-Json -Depth 6
