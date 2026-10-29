#requires -Version 5.1
<# Creates a single owned, short-lived Interactive/Limited scheduled task.
Only a tiny newly named synthetic cache is written. No network or real project
payloads are used. Removes only its exact completed test task after readback. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$taskWorkspace='C:\Users\jlewi\Documents\Codex\2026-10-28\prep'
$taskSid='S-1-5-21-3086848077-1833151167-136462239-1001'
if($env:COMPUTERNAME -ine 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $taskSid){throw 'Wrong host/account'}
$taskHelper=Join-Path $taskWorkspace 'outputs\Project-Library-2026-10-29\Server-Setup'
$taskPython='C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$taskPowerShell=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$taskEvidencePath=Join-Path $PSScriptRoot 'scheduled-job-test-results.json'
$taskExpected=@{
 'Start-MSIJob.ps1'='eaec6e1a4287e44d05426e474f714bb4b58d8624936035df494d1a05d42e8bcb'
 'Invoke-MSIJob.ps1'='a22f70985cb38b8dabe4859134d4efc9705b766d54b5bad639feaa7150a3fd2c'
 'msi_job_runner.py'='b21443925bcb3135cd2869be548dbf68dd770f526d3ce453a0a14584eb0c3896'
 'msi_job_process.py'='004b041547291241d2d4674fd08234e68ba26f43b69b77aeaf6d03d38264a60b'
}
foreach($taskName in $taskExpected.Keys){if((Get-FileHash -LiteralPath (Join-Path $taskHelper $taskName) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpected[$taskName]){throw "Reviewed helper hash changed: $taskName"}}
$taskProject='job-review-20261029-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$taskCacheBase='C:\Users\jlewi\Documents\Codex\MSI-Working-Projects'
$taskCache=Join-Path $taskCacheBase $taskProject
$taskFiles=Join-Path $taskCache 'Files'
function Assert-NoLinks([string]$Path){
 $taskWalk=[IO.Path]::GetFullPath($Path)
 while($taskWalk){if(Test-Path -LiteralPath $taskWalk){if(((Get-Item -LiteralPath $taskWalk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw "Linked fixture path refused: $taskWalk"}};$taskParent=[IO.Directory]::GetParent($taskWalk);$taskWalk=if($taskParent){$taskParent.FullName}else{$null}}
}
function Save-Json([string]$Path,$Data){[IO.File]::WriteAllText($Path,($Data | ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))}
$taskEvidence=[ordered]@{Status='STARTING';ProjectId=$taskProject;CacheRoot=$taskCache;StartedUtc=[DateTime]::UtcNow.ToString('o');ReviewedHashes=$taskExpected;NoNetwork=$true;RealProjectPayloadsUsed=$false;TaskRemoved=$false;Error=$null}
$taskReceipt=$null
try{
 Assert-NoLinks $taskCache
 if(Test-Path -LiteralPath $taskCache){throw 'Refusing existing fixture cache'}
 [IO.Directory]::CreateDirectory($taskFiles) | Out-Null
 $taskIdentity=@{projectId=$taskProject;catalogId='msi-project-library-v1';library='\\MSI\ProjectLibrary';returns='\\MSI\ProjectReturns'}
 $taskOwner=@{format='project-library-owned-v1'};foreach($taskKey in $taskIdentity.Keys){$taskOwner[$taskKey]=$taskIdentity[$taskKey]};Save-Json (Join-Path $taskCache 'OWNER.json') $taskOwner
 $taskState=@{format='project-library-cache-v1';status='READY';files=@();originals=@();directories=@();baselineHead=$null;savedHead=$null};foreach($taskKey in $taskIdentity.Keys){$taskState[$taskKey]=$taskIdentity[$taskKey]};Save-Json (Join-Path $taskCache 'state.json') $taskState
 $taskEntry=@'
import os,subprocess,sys,time
from pathlib import Path
Path('direct-started.txt').write_text(str(os.getpid()))
subprocess.Popen([sys.executable,'-B',str(Path('child.py').absolute())])
Path('direct-finished.txt').write_text('direct process completed')
print('Direct process ended; descendant must retain workspace lock.',flush=True)
'@
 $taskChild=@'
import os,time
from pathlib import Path
Path('descendant-started.txt').write_text(str(os.getpid()))
time.sleep(12)
Path('descendant-finished.txt').write_text('owned fixture child completed')
print('Descendant completed.',flush=True)
'@
 [IO.File]::WriteAllText((Join-Path $taskFiles 'entry.py'),$taskEntry,(New-Object Text.UTF8Encoding($false)))
 [IO.File]::WriteAllText((Join-Path $taskFiles 'child.py'),$taskChild,(New-Object Text.UTF8Encoding($false)))
 Save-Json (Join-Path $taskFiles 'spec.json') @{format='msi-job-spec-v1';projectId=$taskProject;kind='python';entry='entry.py';workingDirectory='.';arguments=@()}
 $taskStartOutput=@(& $taskPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $taskHelper 'Start-MSIJob.ps1') -Mode Start -SpecFile (Join-Path $taskFiles 'spec.json'))
 if($LASTEXITCODE -ne 0){throw 'Start helper failed'}
 $taskReceipt=($taskStartOutput -join [Environment]::NewLine) | ConvertFrom-Json
 if($taskReceipt.JobId -notmatch '^[0-9a-f]{32}$' -or $taskReceipt.TaskName -cne ('MSICodingJob-'+$taskReceipt.JobId) -or $taskReceipt.ProjectId -cne $taskProject){throw 'Unexpected task receipt'}
 $taskEvidence['StartReceipt']=$taskReceipt;$taskEvidence['InitiatingProcessExitedUtc']=[DateTime]::UtcNow.ToString('o')
 $taskStatusPath=Join-Path $taskReceipt.JobDirectory 'status.json'
 $taskDeadline=[DateTime]::UtcNow.AddSeconds(40)
 while(-not (Test-Path -LiteralPath (Join-Path $taskFiles 'descendant-started.txt')) -and [DateTime]::UtcNow -lt $taskDeadline){Start-Sleep -Milliseconds 200}
 if(-not (Test-Path -LiteralPath (Join-Path $taskFiles 'descendant-started.txt'))){throw 'Scheduled descendant did not start'}
 if(Test-Path -LiteralPath (Join-Path $taskFiles 'descendant-finished.txt')){throw 'Missed active descendant verification window'}
 if(-not (Test-Path -LiteralPath (Join-Path $taskFiles 'direct-finished.txt'))){throw 'Direct process did not finish before descendant check'}
 $taskDuring=Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json
 if($taskDuring.status -cne 'RUNNING' -or -not $taskDuring.cacheLockHeld){throw 'Runner did not retain active status/lock during descendant'}
 $taskLocked=@(& $taskPython -B (Join-Path $PSScriptRoot 'probe_job_cache_lock.py') $taskCache --expect blocked)
 if($LASTEXITCODE -ne 0){throw 'Cache lock was not held while descendant ran'}
 $taskEvidence['DuringDescendant']=$taskDuring;$taskEvidence['LockProbeDuring']=($taskLocked -join '') | ConvertFrom-Json
 $taskDeadline=[DateTime]::UtcNow.AddSeconds(40)
 do{Start-Sleep -Milliseconds 200;$taskAfter=Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json}while($taskAfter.status -in @('PREPARED_NOT_REGISTERED','STARTING','RUNNING') -and [DateTime]::UtcNow -lt $taskDeadline)
 if($taskAfter.status -cne 'COMPLETED' -or $taskAfter.exitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $taskFiles 'descendant-finished.txt'))){throw 'Job did not complete after descendant'}
 $taskUnlocked=@(& $taskPython -B (Join-Path $PSScriptRoot 'probe_job_cache_lock.py') $taskCache --expect available)
 if($LASTEXITCODE -ne 0){throw 'Cache lock did not release after completed process tree'}
 $taskEvidence['AfterCompletion']=$taskAfter;$taskEvidence['LockProbeAfter']=($taskUnlocked -join '') | ConvertFrom-Json
 $taskDeadline=[DateTime]::UtcNow.AddSeconds(10)
 do{Start-Sleep -Milliseconds 200;$taskActual=Get-ScheduledTask -TaskPath '\' -TaskName $taskReceipt.TaskName}while([string]$taskActual.State -in @('Running','4') -and [DateTime]::UtcNow -lt $taskDeadline)
 $taskActualInfo=Get-ScheduledTaskInfo -TaskPath '\' -TaskName $taskReceipt.TaskName
 $taskActualPrincipal=$taskActual.Principal.UserId
 if($taskActualPrincipal -notmatch '^S-1-'){$taskActualPrincipal=(New-Object Security.Principal.NTAccount($taskActualPrincipal)).Translate([Security.Principal.SecurityIdentifier]).Value}
 if($taskActualPrincipal -ne $taskSid -or [string]$taskActual.Principal.LogonType -notin @('Interactive','3') -or [string]$taskActual.Principal.RunLevel -notin @('Limited','0')){throw 'Unexpected scheduled task principal'}
 if(@($taskActual.Actions).Count -ne 1 -or $taskActual.Actions[0].Execute -ine $taskPowerShell -or $taskActual.Actions[0].Arguments -notlike ('*-JobId '+$taskReceipt.JobId) -or $taskActual.Actions[0].WorkingDirectory -ine $taskReceipt.JobDirectory){throw 'Task action ownership mismatch; will not remove'}
 if([string]$taskActual.State -in @('Running','4') -or $taskActualInfo.LastTaskResult -ne 0){throw 'Scheduled task has not completed successfully'}
 $taskEvidence['SchedulerReadback']=@{State=[string]$taskActual.State;LastTaskResult=$taskActualInfo.LastTaskResult;PrincipalSid=$taskActualPrincipal;LogonType=[string]$taskActual.Principal.LogonType;RunLevel=[string]$taskActual.Principal.RunLevel;Action=$taskActual.Actions[0].Execute;Arguments=$taskActual.Actions[0].Arguments}
 Unregister-ScheduledTask -TaskPath '\' -TaskName $taskReceipt.TaskName -Confirm:$false
 if(Get-ScheduledTask -TaskPath '\' -TaskName $taskReceipt.TaskName -ErrorAction SilentlyContinue){throw 'Owned test task still registered after removal'}
 $taskEvidence.TaskRemoved=$true;$taskEvidence.Status='PASS';$taskEvidence['CompletedUtc']=[DateTime]::UtcNow.ToString('o')
 Save-Json $taskEvidencePath $taskEvidence
 $taskEvidence | ConvertTo-Json -Depth 12
}catch{
 $taskEvidence.Status='FAILED_REVIEW_REQUIRED';$taskEvidence.Error=$_.Exception.Message;$taskEvidence['CompletedUtc']=[DateTime]::UtcNow.ToString('o');Save-Json $taskEvidencePath $taskEvidence
 Write-Output ($taskEvidence | ConvertTo-Json -Depth 12)
 throw
}
