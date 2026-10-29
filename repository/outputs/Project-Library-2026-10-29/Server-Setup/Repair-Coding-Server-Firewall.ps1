#requires -Version 5.1
<# Fix only the owned rule's unsupported Service=sshd condition. All program,
profile, address and port constraints stay identical. Does not alter service SID,
service lifecycle, credentials, sshd configuration, or unrelated firewall rules. #>
[CmdletBinding()]param([ValidateSet('Inspect','Apply')][string]$Mode='Inspect',[string]$ConfirmStateId)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$OwnedRule='Codex-MSI-Coding-SSH-Private-LocalSubnet-20261029'
$BlockRule='Codex-MSI-Coding-SSH-Block-NonPrivate-20261029'
$DefaultRule='OpenSSH-Server-In-TCP'
$StatePath=Join-Path $PSScriptRoot 'State\SETUP-STATE.json'
$tag=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$reportPath=Join-Path $PSScriptRoot ('Logs\FIREWALL-REPAIR-'+$tag+'.json')
$report=[ordered]@{Format='msi-ssh-owned-firewall-repair-v1';StartedUtc=[DateTime]::UtcNow.ToString('o');Mode=$Mode;Status='CHECKING';Changed=$false;Error=$null}
$lock=$null;$changed=$false;$expectedAfter=$null;$beforeRule=$null;$stateCommitted=$false
function Require([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
function SnapshotRule([string]$Name){
    $rule=Get-NetFirewallRule -PolicyStore PersistentStore -Name $Name -ErrorAction Stop
    $port=$rule|Get-NetFirewallPortFilter;$address=$rule|Get-NetFirewallAddressFilter;$app=$rule|Get-NetFirewallApplicationFilter;$service=$rule|Get-NetFirewallServiceFilter
    $package=if($null -ne $app.PSObject.Properties['Package']){[string]$app.Package}else{''}
    $family=if($null -ne $rule.PSObject.Properties['PackageFamilyName']){[string]$rule.PackageFamilyName}else{''}
    [ordered]@{Name=$rule.Name;DisplayName=$rule.DisplayName;Description=$rule.Description;Enabled=[string]$rule.Enabled;Profile=[string]$rule.Profile;Direction=[string]$rule.Direction;Action=[string]$rule.Action;Protocol=[string]$port.Protocol;LocalPort=@($port.LocalPort);RemotePort=@($port.RemotePort);LocalAddress=@($address.LocalAddress);RemoteAddress=@($address.RemoteAddress);Program=[string]$app.Program;Package=$package;PackageFamilyName=$family;Service=[string]$service.Service}
}
function Same($A,$B){($A|ConvertTo-Json -Depth 10 -Compress) -ceq ($B|ConvertTo-Json -Depth 10 -Compress)}
function AssertRuleScope($Rule){
    Require ($Rule.Name -ceq $OwnedRule -and $Rule.Description -ceq 'Owned by MSI coding setup eb12c122-d728-4dc9-baec-2791aaf38f62') 'Owned rule identity changed.'
    Require ($Rule.Enabled -eq 'True' -and $Rule.Direction -eq 'Inbound' -and $Rule.Action -eq 'Allow' -and $Rule.Profile -eq 'Private') 'Owned rule profile/action drift.'
    Require ($Rule.Protocol -eq 'TCP' -and @($Rule.LocalPort).Count -eq 1 -and $Rule.LocalPort[0] -eq '22' -and @($Rule.RemoteAddress).Count -eq 1 -and $Rule.RemoteAddress[0] -eq 'LocalSubnet') 'Owned rule port/address drift.'
    Require ($Rule.Program -ieq 'C:\Windows\System32\OpenSSH\sshd.exe' -and $Rule.Service -eq 'sshd' -and [string]::IsNullOrEmpty($Rule.Package) -and [string]::IsNullOrEmpty($Rule.PackageFamilyName)) 'Expected exact executable and unsupported service filter.'
}
try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent();$principal=New-Object Security.Principal.WindowsPrincipal($identity)
    Require ($env:COMPUTERNAME -ieq 'MSI' -and $identity.User.Value -ceq $ExpectedSid -and $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -and [Environment]::Is64BitProcess) 'Requires elevated 64-bit PowerShell as existing MSI\jlewi.'
    $cursor=[IO.Path]::GetFullPath($PSScriptRoot)
    while($cursor){Require (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Reparse setup directory refused.';$parent=[IO.Directory]::GetParent($cursor);if($null -eq $parent){break};$cursor=$parent.FullName}
    foreach($path in @($StatePath,(Join-Path $PSScriptRoot '.setup-operation.lock'))){Require (((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Reparse state/lock refused.'}
    $lock=[IO.File]::Open((Join-Path $PSScriptRoot '.setup-operation.lock'),[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $state=Get-Content -LiteralPath $StatePath -Raw|ConvertFrom-Json
    Require ($state.Format -eq 'msi-coding-server-setup-v1' -and $state.StateId -ceq 'eb12c122-d728-4dc9-baec-2791aaf38f62' -and $state.OwnerSid -ceq $ExpectedSid -and $state.Status -eq 'SERVER_STARTED_CLIENT_LOGIN_NOT_TESTED') 'Expected successful owned server state.'
    Require ($state.CreatedOwnedRule -and $state.CreatedBlockRule -and $state.ServiceStartedByHelper -and $state.CapabilityInstalledByHelper) 'Expected helper-owned resources.'
    Require ($null -eq $state.PSObject.Properties['FirewallRepairHistory']) 'Firewall repair already recorded; review before repeating.'
    Require (Same $state.Before (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'State\SETUP-BEFORE.json') -Raw|ConvertFrom-Json)) 'Original before-state differs.'
    Require ((Get-FileHash -LiteralPath 'C:\ProgramData\ssh\sshd_config' -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $state.ConfigSha256) 'Server configuration drift.'
    $beforeRule=SnapshotRule $OwnedRule;$beforeBlock=SnapshotRule $BlockRule;$beforeDefault=SnapshotRule $DefaultRule
    Require ((Same $beforeRule $state.ExpectedOwnedRule) -and (Same $beforeBlock $state.ExpectedBlockRule) -and (Same $beforeDefault $state.ExpectedDefaultRule)) 'Firewall ownership snapshot drift.'
    AssertRuleScope $beforeRule
    $sidText=@(& "$env:SystemRoot\System32\sc.exe" qsidtype sshd);Require ($LASTEXITCODE -eq 0 -and ($sidText -join "`n") -match 'SERVICE_SID_TYPE:\s+NONE') 'Expected verified service SID NONE mismatch.'
    $service=Get-CimInstance Win32_Service -Filter "Name='sshd'"
    Require ($service.State -eq 'Running' -and $service.StartName -ieq 'LocalSystem' -and $service.PathName.Trim('"') -ieq 'C:\Windows\System32\OpenSSH\sshd.exe') 'Unexpected running service identity.'
    $listeners=@(Get-NetTCPConnection -LocalPort 22 -State Listen)
    Require (@($listeners|Where-Object {$_.LocalAddress -eq '0.0.0.0' -and $_.OwningProcess -eq $service.ProcessId}).Count -eq 1) 'Expected service-owned IPv4 listener.'
    $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore);Require ($profiles.Count -eq 3 -and @($profiles|Where-Object {-not $_.Enabled}).Count -eq 0) 'Firewall profiles must stay enabled.'
    Require (@(Get-NetConnectionProfile|Where-Object {$_.InterfaceAlias -eq 'Ethernet' -and [string]$_.NetworkCategory -eq 'Private'}).Count -eq 1) 'Direct Ethernet must be Private.'
    Require (@(Get-NetIPAddress -InterfaceAlias Ethernet -AddressFamily IPv4|Where-Object {$_.IPAddress -eq '192.168.247.1' -and $_.PrefixLength -eq 24}).Count -eq 1) 'Direct Ethernet address changed.'
    $expectedAfter=$beforeRule|ConvertTo-Json -Depth 10|ConvertFrom-Json;$expectedAfter.Service='Any'
    $report['BeforeRule']=$beforeRule;$report['ExpectedAfterRule']=$expectedAfter;$report['SidType']=$sidText;$report['ServicePid']=$service.ProcessId
    if($Mode -eq 'Inspect'){$report.Status='REPAIR_PRECONDITIONS_VERIFIED'}else{
        Require ($ConfirmStateId -ceq $state.StateId) 'Apply requires exact recorded StateId.'
        $backup=Join-Path $PSScriptRoot ('State\SETUP-STATE-before-firewall-repair-'+$tag+'.json')
        [IO.File]::Copy($StatePath,$backup,$false)
        $report['BeforeStateCopy']=$backup;$report.Status='REPAIR_INTENT_RECORDED'
        [IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 16),(New-Object Text.UTF8Encoding($false)))
        Get-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule|Get-NetFirewallServiceFilter|Set-NetFirewallServiceFilter -Service Any|Out-Null
        $changed=$true;$report.Changed=$true
        $after=SnapshotRule $OwnedRule
        Require ((Same $after $expectedAfter) -and (Same (SnapshotRule $BlockRule) $beforeBlock) -and (Same (SnapshotRule $DefaultRule) $beforeDefault)) 'Repair changed more than the intended service filter.'
        $state.ExpectedOwnedRule=$after
        $history=[pscustomobject]@{AppliedUtc=[DateTime]::UtcNow.ToString('o');Reason='Inbox sshd has SID type NONE; removed unsupported service filter while preserving exact executable, Private LocalSubnet TCP22 scope.';PreviousStateCopy=$backup;BeforeRule=$beforeRule;AfterRule=$after;Receipt=$reportPath;Source='https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/configure'}
        $state|Add-Member -NotePropertyName FirewallRepairHistory -NotePropertyValue @($history)
        $temp=$StatePath+'.new-'+[guid]::NewGuid().ToString('N')
        [IO.File]::WriteAllText($temp,($state|ConvertTo-Json -Depth 32),(New-Object Text.UTF8Encoding($false)))
        # PS5.1 can coerce $null to an empty string for the optional backup path.
        # An explicit unique backup avoids that binding failure and retains history.
        $replaceBackup=Join-Path $PSScriptRoot ('State\SETUP-STATE-commit-backup-'+$tag+'.json')
        Require (-not (Test-Path -LiteralPath $replaceBackup)) 'Commit backup path already exists.'
        [IO.File]::Replace($temp,$StatePath,$replaceBackup);$stateCommitted=$true
        $report['CommitBackup']=$replaceBackup
        $report.Status='REPAIRED_CLIENT_RETEST_REQUIRED'
    }
}catch{
    $report.Status='REPAIR_FAILED';$report.Error=$_.Exception.Message
    if($changed -and -not $stateCommitted){
        try{Require (Same (SnapshotRule $OwnedRule) $expectedAfter) 'Current rule drift prevents automatic rollback.';Get-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule|Get-NetFirewallServiceFilter|Set-NetFirewallServiceFilter -Service sshd|Out-Null;Require (Same (SnapshotRule $OwnedRule) $beforeRule) 'Rollback verification failed.';$report['Rollback']='RESTORED_ORIGINAL_RULE'}catch{$report['Rollback']='REVIEW_REQUIRED: '+$_.Exception.Message}
    }
}finally{
    $report['FinishedUtc']=[DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 16),(New-Object Text.UTF8Encoding($false)))
    if($null -ne $lock){$lock.Dispose()}
}
$report|ConvertTo-Json -Depth 16
if($report.Status -eq 'REPAIR_FAILED'){exit 1}
