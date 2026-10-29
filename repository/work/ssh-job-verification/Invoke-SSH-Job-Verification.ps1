#requires -Version 5.1
<# MSI-side fixed controller. Starts only the pinned manual synthetic fixture.
No production fetch, project data, password handling, installation or elevation. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$FixtureId,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$ClientRunId)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$package='C:\Users\jlewi\Documents\Codex\2026-10-28\prep\outputs\Project-Library-2026-10-29'
$expectedControl=Join-Path (Join-Path $package 'HostLibrary') ('SSH-Job-Verification-'+$FixtureId)
if([IO.Path]::GetFullPath($PSScriptRoot) -ine $expectedControl){throw 'Controller must run from its exact published fixture directory.'}
# Read only pinned data before importing the shared, reviewed functions.
$planPath=Join-Path $PSScriptRoot 'SSH-Verification-Plan.json'
$pre=Get-Content -LiteralPath $planPath -Raw|ConvertFrom-Json
if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'SSH-Verification-Common.ps1') -Algorithm SHA256).Hash -ine $pre.CommonSha256){throw 'Common helper hash differs.'}
. (Join-Path $PSScriptRoot 'SSH-Verification-Common.ps1')
$plan=Load-PinnedPlan $PSScriptRoot
Require ($plan.FixtureId -ceq $FixtureId) 'Fixture argument differs.'
Require ((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash -ieq $plan.ControllerSha256) 'Controller hash differs.'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
Require ([Environment]::MachineName -ieq 'MSI' -and $identity.User.Value -ceq $plan.ExpectedSid) 'Authenticated remote host/account identity differs; no job was started.'
$cache=Join-Path 'C:\Users\jlewi\Documents\Codex\MSI-Working-Projects' $plan.ProjectId
Require ($plan.CacheRoot -ceq $cache) 'Unexpected managed synthetic cache.'
$returns=Join-Path (Join-Path $package 'Returns') ('SSH-Job-Verification-'+$FixtureId)
Require ($plan.ReturnsLocal -ceq $returns) 'Unexpected verification receipt root.'
[void](Plain $returns $true);[void](Plain $cache $true)
Require ((Hash-Bytes (Read-Small (Join-Path $returns 'FIXTURE-OWNER.json'))) -ceq $plan.ReturnsOwnerSha256) 'Returns fixture owner differs.'
$gate=[IO.FileStream]::new((Join-Path $returns '.verification.lock'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
$held=New-Object 'Collections.Generic.List[IDisposable]'
$evidence=$null;$result=$null;$exitCode=2
try{
    $entries=@(Get-ChildItem -LiteralPath $returns -Force | Select-Object -ExpandProperty Name)
    Require ($entries.Count -eq 2 -and $entries -contains 'FIXTURE-OWNER.json' -and $entries -contains '.verification.lock') 'Prior/unexpected verification state exists; preserve it for review.'
    $evidence=Join-Path $returns ('Run-'+$ClientRunId)
    Require (-not (Test-Path -LiteralPath $evidence)) 'Fresh result directory already exists.'
    [void][IO.Directory]::CreateDirectory($evidence)
    $result=[ordered]@{Format='msi-ssh-job-acceptance-v1';Status='STARTING';FixtureId=$FixtureId;ClientRunId=$ClientRunId;ProjectId=$plan.ProjectId;StartedUtc=[DateTime]::UtcNow.ToString('o');Computer=[Environment]::MachineName;Account=$identity.Name;Sid=$identity.User.Value;IdentityVerified=$true;ManualSyntheticRunnerBaseline=$true;ProductionFetchPerformed=$false;ProductionDataUsed=$false;SshDisconnectSurvivalTested=$false;Failure=$null}
    New-Json (Join-Path $evidence 'identity.json') $result
    Write-Host ('Verified SSH identity: '+$identity.Name+' on MSI; starting only the reviewed synthetic job.')
    $allowed=@('OWNER.json','state.json','SYNTHETIC-FIXTURE.json','Files\input.txt','Files\entry.py','Files\spec.json')
    Require (@($plan.FixtureFiles).Count -eq 6 -and @($plan.FixtureFiles.RelativePath|Sort-Object -Unique).Count -eq 6) 'Expected exactly six unique pinned fixture files.'
    foreach($row in $plan.FixtureFiles){Require ($row.RelativePath -cin $allowed) 'Unexpected fixture file.';$held.Add((Pin-File (Join-Path $cache $row.RelativePath) $row.Sha256 $row.Bytes))}
    $marker=Read-Json (Join-Path $cache 'SYNTHETIC-FIXTURE.json')
    Require ($marker.fixtureId -ceq $FixtureId -and $marker.projectId -ceq $plan.ProjectId -and $marker.manualRunnerBaseline -eq $true -and $marker.productionFetchPerformed -eq $false -and $marker.productionDataUsed -eq $false) 'Manual synthetic marker differs.'
    $files=Join-Path $cache 'Files';$output=Join-Path $files 'job-result.json';$spec=Join-Path $files 'spec.json'
    Require (-not (Test-Path -LiteralPath $output)) 'Output already exists; this fixture is one-shot.'
    $fileNames=@(Get-ChildItem -LiteralPath $files -Force | Select-Object -ExpandProperty Name)
    Require ($fileNames.Count -eq 3 -and $fileNames -contains 'entry.py' -and $fileNames -contains 'input.txt' -and $fileNames -contains 'spec.json') 'Unexpected fixture Files content.'
    $server=Join-Path $package 'Server-Setup'
    $helperNames=@('Server-Setup/Start-MSIJob.ps1','Server-Setup/Invoke-MSIJob.ps1','Server-Setup/msi_job_runner.py','Server-Setup/msi_job_process.py','Helper/project_library.py')
    Require (@($plan.HelperFiles).Count -eq 5 -and @($plan.HelperFiles.RelativePath|Sort-Object -Unique).Count -eq 5) 'Expected five unique pinned job helper files.'
    foreach($row in $plan.HelperFiles){Require ($row.RelativePath -cin $helperNames) 'Unexpected helper path.';$held.Add((Pin-File (Join-Path $package $row.RelativePath) $row.Sha256 $row.Bytes))}
    $powerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    $startHelper=Join-Path $server 'Start-MSIJob.ps1'
    $arguments=@('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$startHelper,'-Mode','Start','-SpecFile',$spec)
    $info=New-Object Diagnostics.ProcessStartInfo;$info.FileName=$powerShell;$info.Arguments=(($arguments|ForEach-Object{Quote-Arg $_}) -join ' ');$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process;$process.StartInfo=$info;$watch=[Diagnostics.Stopwatch]::StartNew()
    try{
        Require ($process.Start()) 'Could not start fixed job launcher.'
        $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit(20000)){$process.Kill();throw 'Job-start child exceeded 20 seconds; inspect retained state before retrying.'}
        $result.StartChildExitCode=$process.ExitCode;$result.StartChildExitedUtc=[DateTime]::UtcNow.ToString('o')
        $startText=$stdout.GetAwaiter().GetResult();$errorText=$stderr.GetAwaiter().GetResult()
        New-Bytes (Join-Path $evidence 'start.stdout.json') ([Text.Encoding]::UTF8.GetBytes($startText))
        New-Bytes (Join-Path $evidence 'start.stderr.log') ([Text.Encoding]::UTF8.GetBytes($errorText))
        Require ($process.ExitCode -eq 0) 'The job launcher failed; see start receipts.'
    }finally{$process.Dispose()}
    $start=$startText|ConvertFrom-Json
    Require ($start.Status -ceq 'START_REQUESTED_NOT_COMPLETED' -and $start.ProjectId -ceq $plan.ProjectId -and $start.OwnerSid -ceq $plan.ExpectedSid -and $start.LogonType -ceq 'Interactive' -and $start.RunLevel -ceq 'Limited') 'Unexpected start receipt.'
    Require ($start.JobId -cmatch '^[a-f0-9]{32}$' -and $start.TaskName -ceq ('MSICodingJob-'+$start.JobId)) 'Unexpected job/task ID.'
    $jobFolder=Join-Path (Join-Path $package 'ServerJobs\Jobs') $start.JobId
    Require ($start.JobDirectory -ceq $jobFolder) 'Unexpected job directory.'
    $result.JobId=$start.JobId;$result.TaskName=$start.TaskName
    New-Json (Join-Path $evidence 'start-receipt.json') $start
    $status=$null;$task=$null;$taskInfo=$null
    while($watch.Elapsed.TotalSeconds -lt 60){
        $status=Read-Json (Join-Path $jobFolder 'status.json')
        Require ($status.jobId -ceq $start.JobId -and $status.projectId -ceq $plan.ProjectId) 'Runner receipt ownership mismatch.'
        if($status.status -in @('CHILD_FAILED','RUNNER_FAILED_REVIEW_REQUIRED')){throw ('Synthetic job failed: '+$status.status)}
        $task=Get-ScheduledTask -TaskPath '\' -TaskName $start.TaskName -ErrorAction Stop
        $taskInfo=Get-ScheduledTaskInfo -TaskPath '\' -TaskName $start.TaskName
        if($status.status -ceq 'COMPLETED' -and [string]$task.State -eq 'Ready' -and $taskInfo.LastTaskResult -eq 0){break}
        Start-Sleep -Milliseconds 500
    }
    Require ($status.status -ceq 'COMPLETED' -and $status.exitCode -eq 0 -and $status.cacheLockHeld -eq $false -and [string]$task.State -eq 'Ready' -and $taskInfo.LastTaskResult -eq 0) 'Completion was not verified within 60 seconds; no success claimed and no automatic job deletion.'
    $principalSid=if($task.Principal.UserId -like 'S-1-*'){$task.Principal.UserId}else{([Security.Principal.NTAccount]$task.Principal.UserId).Translate([Security.Principal.SecurityIdentifier]).Value}
    $expectedArgs='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $server 'Invoke-MSIJob.ps1')+'" -JobId '+$start.JobId
    Require ($principalSid -ceq $plan.ExpectedSid -and [string]$task.Principal.LogonType -eq 'Interactive' -and [string]$task.Principal.RunLevel -eq 'Limited' -and @($task.Actions).Count -eq 1 -and @($task.Triggers).Count -eq 0) 'Scheduled task principal/action/trigger ownership differs.'
    Require ($task.Actions[0].Execute -ieq $powerShell -and $task.Actions[0].Arguments -ceq $expectedArgs -and $task.Actions[0].WorkingDirectory -ceq $jobFolder) 'Scheduled task action differs from the fixed helper.'
    $outputBytes=Read-Small $output;$value=[Text.Encoding]::UTF8.GetString($outputBytes)|ConvertFrom-Json
    Require ($value.format -ceq 'msi-ssh-job-output-v1' -and $value.fixtureId -ceq $FixtureId -and $value.projectId -ceq $plan.ProjectId -and $value.computer -ieq 'MSI' -and $value.syntheticOnly -eq $true -and $value.inputSha256 -ceq $plan.InputSha256 -and $value.sumOfSquares1Through10000 -eq 333383335000) 'Synthetic output content differs.'
    Require ([DateTimeOffset]::Parse($value.completedUtc) -gt [DateTimeOffset]::Parse($result.StartChildExitedUtc)) 'Job did not outlive its initiating launcher child.'
    New-Bytes (Join-Path $evidence 'job-result.json') $outputBytes
    New-Json (Join-Path $evidence 'runner-final.json') $status
    $result.OutputSha256=Hash-Bytes $outputBytes;$result.OutputBytes=$outputBytes.Length;$result.OutputRelative='job-result.json'
    $result.RunnerStatus='COMPLETED';$result.RunnerExitCode=0;$result.TaskState=[string]$task.State;$result.LastTaskResult=$taskInfo.LastTaskResult;$result.LaunchChildExitedBeforeJobCompletion=$true;$result.Status='PASS';$exitCode=0
}catch{if($null -ne $result){$result.Status='INCOMPLETE';$result.Failure=$_.Exception.Message}else{Write-Error $_}}
finally{
    foreach($handle in $held){$handle.Dispose()};$gate.Dispose()
    if($null -ne $result){$result.CompletedUtc=[DateTime]::UtcNow.ToString('o');New-Json (Join-Path $evidence 'result.json') $result;$result|ConvertTo-Json -Depth 10}
}
exit $exitCode
