$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
# Read-only validation imports only review functions and saves a review snapshot.
. (Join-Path $PSScriptRoot 'inspect_direct_ethernet_install.ps1')
$expectedSetup='e3126a49-e99c-4153-b304-4fa5c8f153d7'
if($o.Status -ne 'INSTALLED' -or $o.SetupId -ne $expectedSetup -or [string]$t.State -ne 'Ready'){throw 'Reviewed installed/stopped task is not ready.'}
if($o.WorkerSha256 -ne '0A5167837A462BA53DB9BA869C7B1EAEAB4A24E5201087206856249C037B94D0'){throw 'Worker differs from reviewed hash.'}
$pre=Get-NetConnectionProfile -InterfaceIndex 6
if($pre.InterfaceAlias -ne 'Ethernet' -or [string]$pre.NetworkCategory -ne 'Private'){throw 'This non-mutating acceptance run expects the cable to be Private already.'}
$receiptDir=Join-Path $Root 'Receipts'
$before=@(Get-ChildItem -LiteralPath $receiptDir -File|Select-Object -ExpandProperty Name)
$started=[DateTime]::UtcNow
$e=[ordered]@{Format='msi-direct-ethernet-scheduled-acceptance-v1';SetupId=$expectedSetup;TaskName=$TaskName;StartedUtc=$started.ToString('o');Scope='One demand-start of the exact already-installed task. No task edits, no Public toggle, no other network/settings changes.';Status='STARTED';Failure=$null;BeforeCategory='Private';AfterCategory=$null;TaskState=$null;TaskResult=$null;NewReceipt=$null;FinishedUtc=$null}
try{
    Start-ScheduledTask -TaskPath '\' -TaskName $TaskName
    $passed=$false
    for($i=0;$i -lt 40;$i++){
        Start-Sleep -Milliseconds 750
        $t=Read-Task;Assert-Task $t $o
        $info=$t|Get-ScheduledTaskInfo
        $new=@(Get-ChildItem -LiteralPath $receiptDir -File|Where-Object {$_.Name -notin $before -and $_.Name -like 'Run-*.json'})
        if($new.Count -gt 1){throw 'More than one new run receipt; cannot attribute exactly.'}
        if($new.Count -eq 1){
            $r=Get-Content -LiteralPath $new[0].FullName -Raw|ConvertFrom-Json
            $e.NewReceipt=[ordered]@{Path=$new[0].FullName;Sha256=(Get-FileHash -LiteralPath $new[0].FullName -Algorithm SHA256).Hash;Data=$r}
            if($r.SetupId -ne $expectedSetup -or $r.Status -ne 'PRIVATE_READBACK_VERIFIED' -or $r.Changed -ne $false -or $r.NewCategory -ne 'Private' -or [DateTime]::Parse($r.StartedUtc).ToUniversalTime() -lt $started.AddSeconds(-1)){throw 'New scheduled-run receipt did not prove the expected unchanged Private result.'}
            if([string]$t.State -eq 'Ready' -and $info.LastTaskResult -eq 0 -and $info.LastRunTime.ToUniversalTime() -ge $started.AddSeconds(-1)){$passed=$true;break}
        }
    }
    $e.TaskState=[string]$t.State;$e.TaskResult=$info.LastTaskResult
    if(-not $passed){throw 'The bounded task acceptance window ended without both a new verified receipt and task result0.'}
    $p=Get-NetConnectionProfile -InterfaceIndex 6
    $e.AfterCategory=[string]$p.NetworkCategory
    if($p.InterfaceAlias -ne 'Ethernet' -or $e.AfterCategory -ne 'Private'){throw 'Final Ethernet category is not Private.'}
    $e.Status='PASSED_SCHEDULED_TASK_RUNTIME'
}catch{$e.Status='FAILED_REVIEW_REQUIRED';$e.Failure=$_.Exception.Message}
finally{
    $e.FinishedUtc=[DateTime]::UtcNow.ToString('o')
    $e|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $PSScriptRoot 'direct-ethernet-scheduled-acceptance.json') -Encoding UTF8
}
$e|ConvertTo-Json -Depth 10
if($e.Status -ne 'PASSED_SCHEDULED_TASK_RUNTIME'){exit 1}
