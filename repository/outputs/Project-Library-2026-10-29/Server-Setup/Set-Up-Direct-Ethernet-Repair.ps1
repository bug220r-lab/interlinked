#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([ValidateSet('Install','Repair','Inspect','Restore','Resume')][string]$Mode='Inspect',[string]$ExpectedSetupId)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$ExpectedGuid=[guid]'2A683B7E-1B94-4858-97C4-48823A0F2AAB'
$Root='C:\ProgramData\MSI-Coding-Direct-Ethernet-20261029'
$TaskName='MSI-Coding-Trusted-Direct-Ethernet-20261029'
$Worker=Join-Path $Root 'Repair-Trusted-Direct-Ethernet.ps1'
$OwnerPath=Join-Path $Root 'OWNER.json'
$PowerShell=Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$Worker+'"'

function Assert-PlainPath([string]$Path){
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Reparse path refused: $p"}}
        $parent=[IO.Directory]::GetParent($p);if($null -eq $parent){break};$p=$parent.FullName
    }
}
function New-Json([string]$Path,$Value){
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 10))
    $s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}
}
function Assert-ProtectedSddl([string]$Sddl){
    $sd=New-Object Security.AccessControl.RawSecurityDescriptor($Sddl)
    if($null -eq $sd.DiscretionaryAcl -or -not($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'Administrative ownership/protected DACL readback failed.'}
    $seen=@()
    foreach($ace in $sd.DiscretionaryAcl){
        $sid=$ace.SecurityIdentifier.Value
        if([string]$ace.AceType -ne 'AccessAllowed' -or $sid -notin @('S-1-5-18','S-1-5-32-544',$ExpectedSid)){throw 'Unexpected protected ACL entry.'}
        if($sid -eq $ExpectedSid -and ($ace.AccessMask -band 0x500D0116)){throw 'Normal user has write/delete/ownership permission on privileged content.'}
        $seen+=$sid
    }
    foreach($sid in @('S-1-5-18','S-1-5-32-544',$ExpectedSid)){if($sid -notin $seen){throw 'Required protected ACL entry missing.'}}
}
function Save-Owner($Value){
    $tmp=Join-Path $Root ('owner-'+[guid]::NewGuid().ToString('N')+'.tmp')
    New-Json $tmp $Value
    [IO.File]::Replace($tmp,$OwnerPath,[NullString]::Value)
}
function Get-Owner {
    Assert-PlainPath $OwnerPath;Assert-PlainPath $Worker
    Assert-ProtectedSddl ((Get-Acl -LiteralPath $Root).Sddl)
    if(-not(Test-Path -LiteralPath $OwnerPath)){throw 'No owned installation exists.'}
    $o=Get-Content -LiteralPath $OwnerPath -Raw|ConvertFrom-Json
    if($o.Format -ne 'msi-direct-ethernet-v1' -or $o.Root -ne $Root -or $o.TaskName -ne $TaskName -or $o.Sid -ne $ExpectedSid -or [guid]$o.InterfaceGuid -ne $ExpectedGuid){throw 'Ownership mismatch.'}
    if($o.WorkerSha256 -ne (Get-FileHash -LiteralPath $Worker -Algorithm SHA256).Hash){throw 'Installed worker hash changed.'}
    return $o
}
function Resolve-TaskSid([string]$Identity){
    if([string]::IsNullOrWhiteSpace($Identity)){throw 'Task identity is blank.'}
    if($Identity -match '^S-1-'){$id=New-Object Security.Principal.SecurityIdentifier($Identity)}
    else{$id=(New-Object Security.Principal.NTAccount($Identity)).Translate([Security.Principal.SecurityIdentifier])}
    return $id.Value
}
function Assert-Task($Task,$Owner){
    if($null -eq $Task){throw 'Owned scheduled task is absent.'}
    $a=@($Task.Actions);$t=@($Task.Triggers)
    if($Task.TaskPath -ne '\' -or $Task.TaskName -ne $TaskName -or $Task.Description -ne ('Owned MSI direct Ethernet repair '+$Owner.SetupId) -or $a.Count -ne 1 -or $a[0].Execute -ne $PowerShell -or $a[0].Arguments -ne $Arguments -or $a[0].WorkingDirectory -ne $Root -or (Resolve-TaskSid $Task.Principal.UserId) -ne $ExpectedSid -or [string]$Task.Principal.LogonType -ne 'Interactive' -or [string]$Task.Principal.RunLevel -ne 'Highest' -or $t.Count -ne 1 -or $t[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger' -or (Resolve-TaskSid $t[0].UserId) -ne $ExpectedSid){throw 'Scheduled task ownership/action/principal/trigger mismatch.'}
}
function Read-Task {Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction SilentlyContinue}
function Invoke-Repair {
    & $PowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Worker -Attempts 5
    if($LASTEXITCODE -ne 0){throw 'Repair did not verify Private. See the protected dated receipt; no further network changes were attempted.'}
}
if($env:COMPUTERNAME -ne 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $ExpectedSid){throw 'Wrong host/account.'}
Assert-PlainPath $Root

if($Mode -eq 'Inspect'){
    $o=if(Test-Path -LiteralPath $OwnerPath){Get-Owner}else{$null}
    [pscustomobject]@{Owner=$o;Task=(Read-Task|Select-Object TaskName,State,Description);ConnectionProfiles=@(Get-NetConnectionProfile|Select-Object InterfaceAlias,InterfaceIndex,NetworkCategory);Receipts=if(Test-Path -LiteralPath (Join-Path $Root 'Receipts')){@(Get-ChildItem -LiteralPath (Join-Path $Root 'Receipts') -File|Select-Object Name,Length,LastWriteTimeUtc)}else{@()}}|ConvertTo-Json -Depth 8
    exit 0
}
if($Mode -eq 'Install'){
    if(Test-Path -LiteralPath $Root){throw 'Protected installation directory already exists. Use Inspect/Repair/Restore; never overwrite it.'}
    if($null -ne (Read-Task)){throw 'Scheduled task name already exists; refusing to replace it.'}
    $src=Join-Path $PSScriptRoot 'Repair-Trusted-Direct-Ethernet.ps1'
    $original=Join-Path $PSScriptRoot 'direct-ethernet-profile.json'
    Assert-PlainPath $src;Assert-PlainPath $original
    $prior=Get-Content -LiteralPath $original -Raw|ConvertFrom-Json
    if($prior.Computer -ne 'MSI' -or [guid]$prior.InterfaceGuid -ne $ExpectedGuid -or $prior.LocalIP -ne '192.168.247.1' -or $prior.PeerIP -ne '192.168.247.2' -or $prior.OldCategory -ne 'Public' -or $prior.Status -ne 'PRIVATE_READBACK_VERIFIED'){throw 'Original observed cable receipt did not match. No installation performed.'}
    $srcHash=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
    $setupId=[guid]::NewGuid().ToString()
    $o=[ordered]@{Format='msi-direct-ethernet-v1';SetupId=$setupId;Computer='MSI';Sid=$ExpectedSid;Root=$Root;TaskName=$TaskName;InterfaceGuid=[string]$ExpectedGuid;WorkerSha256=$srcHash;OriginalReceiptSha256=(Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash;Status='PREPARING';InstalledUtc=[DateTime]::UtcNow.ToString('o');RestoredUtc=$null;Failure=$null}
    # Protect the NEW directory before placing any executable inside. Never change source ACLs.
    New-Item -ItemType Directory -Path $Root|Out-Null
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))}
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($ExpectedSid)),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')))
    Set-Acl -LiteralPath $Root -AclObject $acl
    $o.DirectorySddl=(Get-Acl -LiteralPath $Root).Sddl
    Assert-ProtectedSddl $o.DirectorySddl
    New-Item -ItemType Directory -Path (Join-Path $Root 'Receipts')|Out-Null
    [IO.File]::Copy($src,$Worker,$false)
    [IO.File]::Copy($original,(Join-Path $Root 'ORIGINAL-PROFILE-RECEIPT.json'),$false)
    if((Get-FileHash -LiteralPath $Worker -Algorithm SHA256).Hash -ne $srcHash){throw 'Installed worker did not hash-match.'}
    New-Json $OwnerPath $o
    $registered=$false
    try{
        $action=New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $Root
        $trigger=New-ScheduledTaskTrigger -AtLogOn -User $ExpectedSid
        $principal=New-ScheduledTaskPrincipal -UserId $ExpectedSid -LogonType Interactive -RunLevel Highest
        $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description ('Owned MSI direct Ethernet repair '+$setupId)|Out-Null
        $registered=$true
        # Protected task ACL: only Administrators/SYSTEM may alter its elevated action.
        $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
        $registeredTask=$scheduler.GetFolder('\').GetTask($TaskName)
        $registeredTask.SetSecurityDescriptor(('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$ExpectedSid+')'),0x10)
        $o.TaskSddl=$registeredTask.GetSecurityDescriptor(7)
        Assert-ProtectedSddl $o.TaskSddl
        Assert-Task (Read-Task) ([pscustomobject]$o)
        $o.Status='INSTALLED';Save-Owner $o
    }catch{
        $o.Status='INSTALL_FAILED';$o.Failure=$_.Exception.Message;Save-Owner $o
        if($registered){$task=Read-Task;Assert-Task $task ([pscustomobject]$o);if([string]$task.State -ne 'Running'){Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false}}
        throw
    }
    Invoke-Repair
    Write-Output ('Installed and verified direct Ethernet repair. Protected receipts: '+$Root+'\Receipts')
    exit 0
}
$o=Get-Owner
$task=Read-Task
if($Mode -eq 'Resume'){
    if([string]::IsNullOrWhiteSpace($ExpectedSetupId) -or ([guid]$ExpectedSetupId).ToString() -ne $o.SetupId -or $o.Status -ne 'PREPARING'){throw 'Resume requires the reviewed exact setup ID and PREPARING state.'}
    Assert-Task $task $o
    if([string]$task.State -eq 'Running'){throw 'The repair task is running; no state changes permitted.'}
    $original=Join-Path $PSScriptRoot 'direct-ethernet-profile.json'
    $copy=Join-Path $Root 'ORIGINAL-PROFILE-RECEIPT.json'
    Assert-PlainPath $original;Assert-PlainPath $copy
    if((Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash -ne $o.OriginalReceiptSha256 -or (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -ne $o.OriginalReceiptSha256){throw 'Original receipt evidence changed.'}
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
    $taskSddl=$scheduler.GetFolder('\').GetTask($TaskName).GetSecurityDescriptor(7)
    Assert-ProtectedSddl $taskSddl
    Assert-PlainPath (Join-Path $Root '.run.lock')
    $runLock=[IO.File]::Open((Join-Path $Root '.run.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try{
        $o=Get-Owner;$task=Read-Task;Assert-Task $task $o
        if($o.SetupId -ne $ExpectedSetupId -or $o.Status -ne 'PREPARING' -or [string]$task.State -eq 'Running'){throw 'Owner/task state changed before the recovery lock was acquired.'}
        # Preserve the exact original PREPARING bytes before committing completion.
        $before=Join-Path $Root ('Receipts\Resume-before-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')+'-'+[guid]::NewGuid().ToString('N')+'.json')
        [IO.File]::Copy($OwnerPath,$before,$false)
        if((Get-FileHash -LiteralPath $before -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $OwnerPath -Algorithm SHA256).Hash){throw 'Original PREPARING snapshot verification failed.'}
        $o|Add-Member -NotePropertyName TaskSddl -NotePropertyValue $taskSddl -Force
        $o|Add-Member -NotePropertyName ResumedUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        $o|Add-Member -NotePropertyName ResumeBeforePath -NotePropertyValue $before -Force
        $o.Status='INSTALLED';$o.Failure=$null;Save-Owner $o
        $check=Get-Owner
        if($check.Status -ne 'INSTALLED' -or $check.SetupId -ne $ExpectedSetupId){throw 'Resume state readback failed.'}
    }finally{$runLock.Dispose()}
    # The worker takes the same lock. Never invoke it while recovery holds it.
    Invoke-Repair
    Write-Output 'Existing protected installation resumed and current cable verified; task/action/ACL/worker unchanged.'
    exit 0
}
if($Mode -eq 'Repair'){
    if($o.Status -ne 'INSTALLED'){throw 'Installation is not active.'}
    Assert-Task $task $o
    Invoke-Repair
    exit 0
}
if($Mode -eq 'Restore'){
    if($o.Status -ne 'INSTALLED'){throw 'Installation is not active; inspect first.'}
    Assert-Task $task $o
    if([string]$task.State -eq 'Running'){throw 'The repair task is running. Retry Restore once it has stopped.'}
    Assert-PlainPath (Join-Path $Root '.run.lock')
    $runLock=[IO.File]::Open((Join-Path $Root '.run.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try{
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false
        if($null -ne (Read-Task)){throw 'Task removal readback failed.'}
        $o.Status='RESTORED_TASK_REMOVED';$o.RestoredUtc=[DateTime]::UtcNow.ToString('o');Save-Owner $o
        # No profile reversal: other work may now depend on the verified Private link.
        # This restore removes ONLY the owned automatic repair. Keep all evidence.
        New-Json (Join-Path $Root ('Receipts\Restore-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')+'.json')) $o
    }finally{$runLock.Dispose()}
    Write-Output 'Owned logon repair removed. Network category and protected evidence retained.'
}
