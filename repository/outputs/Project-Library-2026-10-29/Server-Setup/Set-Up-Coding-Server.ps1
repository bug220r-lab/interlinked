#requires -Version 5.1
<#
Prepare-only until invoked with -Mode Apply. Uses the Windows inbox capability.
No new account, password handling, router change, public-network allow rule,
cloud service, private-key export, or remote command execution is performed.
Restore requires an exact recorded StateId and unchanged owned resources.
#>
[CmdletBinding()]
param(
    [ValidateSet('Inspect','Apply','Resume','RetryNative','Restore')][string]$Mode = 'Inspect',
    [string]$StateDirectory = '',
    [string]$ConfirmStateId,
    [switch]$RemoveInstalledCapability
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($StateDirectory)) { $StateDirectory = Join-Path $PSScriptRoot 'State' }
$ExpectedComputer = 'MSI'
$ExpectedSid = 'S-1-5-21-3086848077-1833151167-136462239-1001'
$AllowedUser = 'jlewi'
$Capability = 'OpenSSH.Server~~~~0.0.1.0'
$DefaultRule = 'OpenSSH-Server-In-TCP'
$OwnedRule = 'Codex-MSI-Coding-SSH-Private-LocalSubnet-20261029'
$BlockRule = 'Codex-MSI-Coding-SSH-Block-NonPrivate-20261029'
$OpenSshDirectory = 'C:\Windows\System32\OpenSSH'
$ConfigPath = 'C:\ProgramData\ssh\sshd_config'
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
$StatePath = Join-Path $StateDirectory 'SETUP-STATE.json'
$BeforePath = Join-Path $StateDirectory 'SETUP-BEFORE.json'
$Utf8 = New-Object Text.UTF8Encoding($false)

function Assert-True([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-LocalPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    Assert-True ($full.StartsWith('C:\',[StringComparison]::OrdinalIgnoreCase)) 'Expected a local C: path.'
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            Assert-True (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) "Link/reparse path refused: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }; $cursor = $parent.FullName
    }
}
function Write-State($Value,[string]$Path=$StatePath) {
    Assert-LocalPath $Path
    $temp = $Path + '.new-' + [guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText($temp,($Value | ConvertTo-Json -Depth 16),$Utf8)
    Move-Item -LiteralPath $temp -Destination $Path -Force
}
function File-Hash([string]$Path) { if (Test-Path -LiteralPath $Path -PathType Leaf) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null } }
function Rule-Snapshot([string]$Name) {
    $rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $rule) { return $null }
    $port = $rule | Get-NetFirewallPortFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $app = $rule | Get-NetFirewallApplicationFilter
    $service = $rule | Get-NetFirewallServiceFilter
    $package = if ($null -ne $app.PSObject.Properties['Package']) { [string]$app.Package } else { '' }
    $family = if ($null -ne $rule.PSObject.Properties['PackageFamilyName']) { [string]$rule.PackageFamilyName } else { '' }
    [ordered]@{ Name=$rule.Name;DisplayName=$rule.DisplayName;Description=$rule.Description;Enabled=[string]$rule.Enabled;Profile=[string]$rule.Profile;Direction=[string]$rule.Direction;Action=[string]$rule.Action;Protocol=[string]$port.Protocol;LocalPort=@($port.LocalPort);RemotePort=@($port.RemotePort);LocalAddress=@($address.LocalAddress);RemoteAddress=@($address.RemoteAddress);Program=[string]$app.Program;Package=$package;PackageFamilyName=$family;Service=[string]$service.Service }
}
function Same-Rule($A,$B) {
    if ($null -eq $A -or $null -eq $B) { return ($null -eq $A -and $null -eq $B) }
    (($A | ConvertTo-Json -Depth 8 -Compress) -eq ($B | ConvertTo-Json -Depth 8 -Compress))
}
function Classify-ResumeNativeProcesses($Processes,[int]$QueryParentId=0,[DateTime]$QueryStartedUtc=[DateTime]::MinValue) {
    $external=@();$queryHosts=@()
    foreach($process in @($Processes)){
        $created=([DateTimeOffset]$process.CreationDate).UtcDateTime
        $item=[pscustomobject]@{Id=[int]$process.ProcessId;ProcessName=[string]$process.Name;ParentId=[int]$process.ParentProcessId;CreatedUtc=$created.ToString('o')}
        # Only this invocation's newly spawned direct child from its completed
        # read-only capability query may linger. Never exempt preexisting hosts,
        # an external parent, dism.exe, or an unknown process birth time.
        if($QueryParentId -gt 0 -and $process.Name -ieq 'DismHost.exe' -and [int]$process.ParentProcessId -eq $QueryParentId -and $created -ge $QueryStartedUtc){$queryHosts+=$item}else{$external+=$item}
    }
    [pscustomobject]@{External=$external;OwnReadOnlyQueryHosts=$queryHosts}
}
function Resume-SystemFacts([int]$QueryParentId=0,[DateTime]$QueryStartedUtc=[DateTime]::MinValue) {
    $os=Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $boot=([DateTimeOffset]$os.LastBootUpTime).UtcDateTime
    $pending=@()
    foreach($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')){
        if(Test-Path -LiteralPath $key){$pending+=$key}
    }
    $installer=$null
    try {
        # Read-only WUA properties. IsBusy is a point-in-time check, not a lock;
        # Windows still arbitrates any later servicing race.
        $installer=New-Object -ComObject Microsoft.Update.Installer
        $busy=[bool]$installer.IsBusy
        $reboot=[bool]$installer.RebootRequiredBeforeInstallation
    } finally {if($null -ne $installer){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)}}
    $native=@(Get-CimInstance -ClassName Win32_Process -Filter "Name='dism.exe' OR Name='DismHost.exe'" -ErrorAction Stop)
    $classified=Classify-ResumeNativeProcesses $native $QueryParentId $QueryStartedUtc
    [ordered]@{BootUtc=$boot.ToString('o');CapturedUtc=[DateTime]::UtcNow.ToString('o');NativeProcesses=@($classified.External);OwnReadOnlyQueryHosts=@($classified.OwnReadOnlyQueryHosts);PendingServicingKeys=$pending;WindowsUpdateBusy=$busy;WindowsUpdateRequiresReboot=$reboot;Port22Listeners=@(Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess)}
}
function Assert-CleanNativeRecovery($Saved,$OriginalBefore,$Current,$Facts,[string]$Confirmation) {
    Assert-True ($Saved.Format -eq 'msi-coding-server-setup-v1' -and $Saved.Computer -ieq $ExpectedComputer -and $Saved.OwnerSid -eq $ExpectedSid -and $Saved.AllowedUser -eq $AllowedUser) 'Saved setup ownership does not match.'
    Assert-True ($Confirmation -ceq $Saved.StateId) 'Native recovery requires the exact recorded StateId.'
    Assert-True $Saved.CapabilityInstallAttempted 'No original native capability installation attempt is recorded.'
    Assert-True (-not $Saved.CapabilityInstalledByHelper -and -not $Saved.CreatedDefaultRuleDisabled -and -not $Saved.CreatedOwnedRule -and -not $Saved.ServiceStartedByHelper -and $null -eq $Saved.ConfigSha256 -and @($Saved.CreatedHostKeyFiles).Count -eq 0 -and @($Saved.HostKeyFingerprints).Count -eq 0) 'Setup progressed beyond the supported absent-state recovery point.'
    Assert-True (($Saved.Before|ConvertTo-Json -Depth 32 -Compress) -ceq ($OriginalBefore|ConvertTo-Json -Depth 32 -Compress)) 'Original before-state record does not match the saved provenance.'
    Assert-True ($OriginalBefore.CapabilityState -eq 'NotPresent' -and $null -eq $OriginalBefore.Service -and -not $OriginalBefore.ConfigExists -and $null -eq $OriginalBefore.DefaultRule -and $null -eq $OriginalBefore.OwnedRule -and $null -eq $OriginalBefore.BlockRule -and @($OriginalBefore.OpenSshFiles).Count -eq 0) 'Original absent-server baseline does not match.'
    Assert-True (@($Facts.NativeProcesses).Count -eq 0 -and -not $Facts.WindowsUpdateBusy) 'Another native servicing operation is active; review and retry later.'
    Assert-True (@($Facts.PendingServicingKeys).Count -eq 0 -and -not $Facts.WindowsUpdateRequiresReboot) 'Windows reports pending servicing/reboot state; review it before resuming.'
    Assert-True (@($Facts.Port22Listeners).Count -eq 0) 'TCP 22 is already in use.'
    Assert-True ($Current.CapabilityState -eq 'NotPresent' -and $null -eq $Current.Service -and -not $Current.ConfigExists -and $null -eq $Current.ConfigSha256 -and $null -eq $Current.DefaultRule -and $null -eq $Current.OwnedRule -and @($Current.OpenSshFiles).Count -eq 0) 'Native recovery supports only the inspected clean NotPresent state; installed or new SSH artifacts need separate review.'
    Assert-True ($Saved.CreatedBlockRule -and (Same-Rule $Current.BlockRule $Saved.ExpectedBlockRule)) 'Original non-private block rule changed or disappeared.'
    Assert-True (@($Current.CompetingAllowRules).Count -eq 0) 'New competing desktop SSH allow rules need review.'
    Assert-True (@($Current.FirewallProfiles).Count -eq 3 -and @($Current.FirewallProfiles|Where-Object {-not $_.Enabled}).Count -eq 0) 'All Windows Firewall profiles must already be enabled.'
    Assert-True (@($Current.ConnectionProfiles|Where-Object {[string]$_.NetworkCategory -in @('1','Private')}).Count -ge 1) 'No current Private network exists.'
}
function Assert-ResumePreconditions($Saved,$OriginalBefore,$Current,$Facts,[string]$Confirmation) {
    Assert-CleanNativeRecovery $Saved $OriginalBefore $Current $Facts $Confirmation
    Assert-True ($Saved.Status -eq 'INSTALLING_CAPABILITY') 'Resume supports only a reboot-interrupted capability installation.'
    Assert-True ($null -eq $Saved.PSObject.Properties['NativeRetryHistory'] -or @($Saved.NativeRetryHistory).Count -eq 0) 'RetryNative was already committed; Resume cannot bypass its one-attempt limit.'
    $lastAttempt=[DateTimeOffset]::Parse($Saved.StartedUtc).UtcDateTime
    if($null -ne $Saved.PSObject.Properties['ResumeHistory']){
        foreach($attempt in @($Saved.ResumeHistory)){$at=[DateTimeOffset]::Parse($attempt.StartedUtc).UtcDateTime;if($at -gt $lastAttempt){$lastAttempt=$at}}
    }
    Assert-True ([DateTimeOffset]::Parse($Facts.BootUtc).UtcDateTime -gt $lastAttempt) 'A verified reboot after the last setup attempt is required.'
}
function Add-RetryNativeFacts($Facts) {
    $Facts['OriginalFailedProcessIds']=@(Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId=4036 OR ProcessId=9484' -ErrorAction Stop|Select-Object ProcessId,Name,CreationDate)
    $Facts['FailedStaging']=@()
    foreach($path in @('C:\Windows\CbsTemp\31287270_1102279589','C:\Windows\CbsTemp\31287270_1102279589\FodWU','C:\Windows\CbsTemp\{7871F405-1B0B-4970-A1E5-5EDFE8B390FF}')){
        Assert-LocalPath $path
        try {
            $item=Get-Item -LiteralPath $path -Force -ErrorAction Stop
            $Facts['FailedStaging']+= [pscustomobject]@{Path=$path;Status='PRESENT';Attributes=[string]$item.Attributes}
        } catch {
            if($_.CategoryInfo.Category -ne 'ObjectNotFound'){throw}
            $Facts['FailedStaging']+= [pscustomobject]@{Path=$path;Status='ABSENT'}
        }
    }
    return $Facts
}
function Assert-RetryNativePreconditions($Saved,$OriginalBefore,$Current,$Facts,$PreviousResult,$Evidence,[string]$Confirmation) {
    Assert-CleanNativeRecovery $Saved $OriginalBefore $Current $Facts $Confirmation
    Assert-True ($Saved.Status -eq 'FAILED_REVIEW_REQUIRED' -and $Saved.Failure.Trim() -ceq 'Cannot create a file when that file already exists.') 'RetryNative supports only this reviewed terminal failure.'
    Assert-True ($null -eq $Saved.PSObject.Properties['NativeRetryHistory'] -or @($Saved.NativeRetryHistory).Count -eq 0) 'The one permitted RetryNative attempt has already been committed; no retry loop is allowed.'
    Assert-True (@($Saved.ResumeHistory).Count -eq 1) 'Unexpected prior attempt history; review it before recovery.'
    Assert-True ($PreviousResult.Mode -eq 'Resume' -and $PreviousResult.Status -eq 'FAILED' -and $PreviousResult.Error -ceq $Saved.Failure -and $PreviousResult.StatePath -ieq $StatePath) 'Terminal result does not match the failed owned attempt.'
    $attemptTime=[DateTimeOffset]::Parse($Saved.ResumeHistory[0].StartedUtc).UtcDateTime
    $finished=[DateTimeOffset]::Parse($PreviousResult.FinishedUtc).UtcDateTime
    Assert-True ([DateTimeOffset]::Parse($PreviousResult.StartedUtc).UtcDateTime -le $attemptTime -and $finished -gt $attemptTime -and [DateTimeOffset]::Parse($Facts.CapturedUtc).UtcDateTime -gt $finished) 'The recorded failure is not a completed prior attempt.'
    Assert-True ($Evidence.Format -eq 'msi-ssh-terminal-failure-v1' -and $Evidence.StateId -ceq $Saved.StateId -and $Evidence.TerminalResult.HResult -eq '0x800700b7' -and $Evidence.TerminalResult.FinishedUtc -ceq $PreviousResult.FinishedUtc -and $Evidence.CurrentInspection.CapabilityState -eq 'NotPresent') 'Reviewed terminal failure evidence does not match.'
    Assert-True (@($Facts.OriginalFailedProcessIds).Count -eq 0) 'A prior installer process ID is present; review its identity before retrying.'
    $expectedPaths=@('C:\Windows\CbsTemp\31287270_1102279589','C:\Windows\CbsTemp\31287270_1102279589\FodWU','C:\Windows\CbsTemp\{7871F405-1B0B-4970-A1E5-5EDFE8B390FF}')
    Assert-True (@($Facts.FailedStaging).Count -eq $expectedPaths.Count) 'Failed-staging absence evidence is incomplete.'
    foreach($path in $expectedPaths){
        $entry=@($Facts.FailedStaging|Where-Object {$_.Path -ieq $path})
        Assert-True ($entry.Count -eq 1 -and $entry[0].Status -eq 'ABSENT') 'Failed native staging remains or is uncertain; do not manipulate it automatically.'
    }
}
function Service-Snapshot {
    $service = Get-Service -Name sshd -ErrorAction SilentlyContinue
    if ($null -eq $service) { return $null }
    $registry = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\sshd'
    [ordered]@{ Status=[string]$service.Status;StartType=[string]$service.StartType;ImagePath=[string]$registry.ImagePath;ObjectName=[string]$registry.ObjectName }
}
function Port-Includes22($Values) {
    foreach ($value in @($Values)) {
        if ([string]$value -in @('Any','22')) { return $true }
        if ([string]$value -match '^(\d+)-(\d+)$' -and 22 -ge [int]$Matches[1] -and 22 -le [int]$Matches[2]) { return $true }
    }
    return $false
}
function Competing-SshAllowRules {
    $filters = @(Get-NetFirewallPortFilter -PolicyStore ActiveStore | Where-Object { ([string]$_.Protocol -in @('TCP','6','Any','256')) -and (Port-Includes22 $_.LocalPort) })
    foreach ($filter in $filters) {
        foreach ($rule in @(Get-NetFirewallRule -AssociatedNetFirewallPortFilter $filter | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' })) {
            $app=$rule | Get-NetFirewallApplicationFilter;$svc=$rule | Get-NetFirewallServiceFilter
            if (([string]$app.Program -eq 'Any' -or [string]$app.Program -ieq (Join-Path $OpenSshDirectory 'sshd.exe')) -and ([string]$svc.Service -in @('Any','sshd'))) {
                $package = if ($null -ne $app.PSObject.Properties['Package']) { [string]$app.Package } else { '' }
                $family = if ($null -ne $rule.PSObject.Properties['PackageFamilyName']) { [string]$rule.PackageFamilyName } else { '' }
                # Package SID and package-family identity are application conditions,
                # even when Program/Service are Any. Inbox sshd is not a Store package.
                $desktop=([string]::IsNullOrWhiteSpace($package) -or $package -eq 'Any') -and ([string]::IsNullOrWhiteSpace($family) -or $family -eq 'Any')
                [pscustomobject]@{Name=$rule.Name;Profile=[string]$rule.Profile;Program=[string]$app.Program;Package=$package;PackageFamilyName=$family;Service=[string]$svc.Service;LocalPort=@($filter.LocalPort);AppliesToDesktopSshd=$desktop}
            }
        }
    }
}
function Expanded-RuleSnapshot([string]$Name) {
    $rule=Get-NetFirewallRule -PolicyStore ActiveStore -Name $Name -ErrorAction Stop
    $exclude=@('CimClass','CimInstanceProperties','CimSystemProperties')
    [ordered]@{
        Rule=($rule | Select-Object * -ExcludeProperty $exclude)
        Application=($rule | Get-NetFirewallApplicationFilter | Select-Object * -ExcludeProperty $exclude)
        Service=($rule | Get-NetFirewallServiceFilter | Select-Object * -ExcludeProperty $exclude)
        Port=($rule | Get-NetFirewallPortFilter | Select-Object * -ExcludeProperty $exclude)
        Address=($rule | Get-NetFirewallAddressFilter | Select-Object * -ExcludeProperty $exclude)
        Security=($rule | Get-NetFirewallSecurityFilter | Select-Object * -ExcludeProperty $exclude)
        Interface=($rule | Get-NetFirewallInterfaceFilter | Select-Object * -ExcludeProperty $exclude)
    }
}
function Assert-OwnedService {
    $service = Service-Snapshot
    Assert-True ($null -ne $service) 'Expected the inbox sshd service.'
    $image = $service.ImagePath.Trim('"')
    Assert-True ($image -ieq (Join-Path $OpenSshDirectory 'sshd.exe')) 'sshd service binary path changed; refusing service changes.'
    Assert-True ($service.ObjectName -ieq 'LocalSystem') 'sshd service identity changed; refusing service changes.'
}
function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    Assert-True ($env:COMPUTERNAME -ieq $ExpectedComputer) "Run only on $ExpectedComputer."
    Assert-True ($identity.User.Value -eq $ExpectedSid) 'Use elevation of MSI\jlewi, not another administrator account.'
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    Assert-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run this reviewed helper in an elevated PowerShell window.'
    Assert-True ([Environment]::Is64BitProcess) 'Use 64-bit PowerShell.'
}
function Snapshot-Current {
    $cap = Get-WindowsCapability -Online -Name $Capability
    $ruleCandidates=@(Competing-SshAllowRules)
    $competing=@($ruleCandidates | Where-Object AppliesToDesktopSshd)
    [ordered]@{ Computer=$env:COMPUTERNAME;Account=[Security.Principal.WindowsIdentity]::GetCurrent().Name;Sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;CapturedUtc=[DateTime]::UtcNow.ToString('o');CapabilityName=$Capability;CapabilityState=[string]$cap.State;Service=(Service-Snapshot);ConfigExists=(Test-Path -LiteralPath $ConfigPath -PathType Leaf);ConfigSha256=(File-Hash $ConfigPath);DefaultRule=(Rule-Snapshot $DefaultRule);OwnedRule=(Rule-Snapshot $OwnedRule);BlockRule=(Rule-Snapshot $BlockRule);CompetingAllowRules=$competing;CompetingRuleDetails=@($competing | ForEach-Object { Expanded-RuleSnapshot $_.Name });NonDesktopPackageRules=@($ruleCandidates | Where-Object { -not $_.AppliesToDesktopSshd });FirewallProfiles=@(Get-NetFirewallProfile | Select-Object Name,Enabled);ConnectionProfiles=@(Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory);OpenSshFiles=@(Get-ChildItem -LiteralPath 'C:\ProgramData\ssh' -File -ErrorAction SilentlyContinue | Select-Object Name,Length,LastWriteTimeUtc) }
}
function Set-NewPrivateFileAcl([string]$Path) {
    # Only newly created helper configuration or newly generated host-key files.
    Assert-LocalPath $Path
    $acl = New-Object Security.AccessControl.FileSecurity
    $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl.SetOwner($admins);$acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @($admins,$system)) { $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow'))) }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Disable-NewDefaultRule($State) {
    if ($null -eq $State.Before.DefaultRule -and $null -ne (Rule-Snapshot $DefaultRule)) {
        Disable-NetFirewallRule -PolicyStore PersistentStore -Name $DefaultRule | Out-Null
        $State.CreatedDefaultRuleDisabled = $true
        $State.ExpectedDefaultRule = Rule-Snapshot $DefaultRule
        Write-State $State
    }
}
function Complete-Run([string]$Status,[string]$ErrorMessage='') {
    $result=[ordered]@{Mode=$Mode;Status=$Status;Computer=$env:COMPUTERNAME;StartedUtc=$script:RunStartedUtc;FinishedUtc=[DateTime]::UtcNow.ToString('o');Transcript=$script:RunLogPath;Snapshot=$script:RunSnapshotPath;StatePath=$StatePath;Error=$ErrorMessage;ClientLoginTested=$false}
    [IO.File]::WriteAllText($script:RunResultPath,($result | ConvertTo-Json -Depth 6),$Utf8)
    if ($script:TranscriptStarted) { Stop-Transcript | Out-Null;$script:TranscriptStarted=$false }
    if ($null -ne $script:SetupLock) { $script:SetupLock.Dispose();$script:SetupLock=$null }
}

$script:RunStartedUtc=[DateTime]::UtcNow.ToString('o')
$logDirectory=Join-Path $PSScriptRoot 'Logs'
Assert-LocalPath $logDirectory
if (-not (Test-Path -LiteralPath $logDirectory)) { New-Item -ItemType Directory -Path $logDirectory | Out-Null }
$runTag=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,10)
$script:RunLogPath=Join-Path $logDirectory ($Mode+'-'+$runTag+'.txt')
$script:RunResultPath=Join-Path $logDirectory ($Mode+'-'+$runTag+'-RESULT.json')
$script:RunSnapshotPath=Join-Path $logDirectory ($Mode+'-'+$runTag+'-SNAPSHOT.json')
$script:TranscriptStarted=$false
$script:SetupLock=$null
trap {
    try { Complete-Run 'FAILED' $_.Exception.Message } catch { }
    Write-Error $_
    exit 1
}
Start-Transcript -LiteralPath $script:RunLogPath -NoClobber | Out-Null
$script:TranscriptStarted=$true

Assert-Administrator
$setupLockPath=Join-Path $PSScriptRoot '.setup-operation.lock'
Assert-LocalPath $setupLockPath
$script:SetupLock=[IO.File]::Open($setupLockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
Assert-LocalPath $StateDirectory
Assert-True ($StateDirectory.StartsWith(([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)) 'State directory must stay inside this setup-helper folder.'
Assert-LocalPath $OpenSshDirectory
Assert-LocalPath $ConfigPath
if ($Mode -eq 'Inspect') {
    $inspection=Snapshot-Current
    [IO.File]::WriteAllText($script:RunSnapshotPath,($inspection | ConvertTo-Json -Depth 16),$Utf8)
    $inspection | ConvertTo-Json -Depth 12
    Complete-Run 'INSPECTION_COMPLETE'
    return
}

if ($Mode -eq 'Restore') {
    Assert-True (Test-Path -LiteralPath $StatePath -PathType Leaf) 'No saved setup state exists.'
    $saved = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    Assert-True ($saved.Format -eq 'msi-coding-server-setup-v1' -and $saved.OwnerSid -eq $ExpectedSid -and $saved.Computer -ieq $ExpectedComputer) 'State ownership does not match.'
    Assert-True ($ConfirmStateId -ceq $saved.StateId) 'Supply -ConfirmStateId with the exact saved StateId for restoration.'
    Assert-True ($null -eq $saved.Before.Service -and -not $saved.Before.ConfigExists -and $saved.Before.CapabilityState -eq 'NotPresent') 'Restore supports only the absent-server baseline owned by this helper.'
    if (Test-Path -LiteralPath $ConfigPath) { Assert-True ((File-Hash $ConfigPath) -eq $saved.ConfigSha256) 'Config changed since setup; preserve it and review restoration manually.' }
    if ($null -ne (Rule-Snapshot $OwnedRule)) { Assert-True (Same-Rule (Rule-Snapshot $OwnedRule) $saved.ExpectedOwnedRule) 'Owned firewall rule changed; refusing restoration.' }
    if ($null -ne (Rule-Snapshot $DefaultRule)) { Assert-True ($saved.CreatedDefaultRuleDisabled -and (Same-Rule (Rule-Snapshot $DefaultRule) $saved.ExpectedDefaultRule)) 'Default firewall rule ownership/state changed; refusing restoration.' }
    if ($null -ne (Rule-Snapshot $BlockRule)) { Assert-True ($saved.CreatedBlockRule -and (Same-Rule (Rule-Snapshot $BlockRule) $saved.ExpectedBlockRule)) 'Non-private block rule ownership/state changed; refusing restoration.' }
    if ($null -ne (Service-Snapshot)) { Assert-OwnedService;Stop-Service sshd -Force;Set-Service sshd -StartupType Disabled }
    if ($null -ne (Rule-Snapshot $OwnedRule)) { Remove-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule }
    if ($saved.CreatedDefaultRuleDisabled -and $null -ne (Rule-Snapshot $DefaultRule)) { Remove-NetFirewallRule -PolicyStore PersistentStore -Name $DefaultRule }
    if ($saved.CreatedBlockRule -and $null -ne (Rule-Snapshot $BlockRule)) { Remove-NetFirewallRule -PolicyStore PersistentStore -Name $BlockRule }
    if (Test-Path -LiteralPath $ConfigPath) {
        $retained = Join-Path $StateDirectory ('restored-sshd_config-'+$saved.StateId+'.txt')
        Assert-True (-not (Test-Path -LiteralPath $retained)) 'Preserved restore config already exists.'
        Move-Item -LiteralPath $ConfigPath -Destination $retained
    }
    if ($RemoveInstalledCapability) {
        Assert-True $saved.CapabilityInstallAttempted 'Capability is not owned by this helper.'
        $result = Remove-WindowsCapability -Online -Name $Capability
        $saved.RestoreCapabilityResult = [pscustomobject]@{ RestartNeeded=$result.RestartNeeded;Online=$result.Online }
    }
    $saved.Status='RESTORED_OWNED_ACCESS';$saved.RestoredUtc=[DateTime]::UtcNow.ToString('o')
    Write-State $saved
    Write-Output 'Owned SSH access restored/disabled. Host keys were preserved. Capability removal occurs only with -RemoveInstalledCapability.'
    Complete-Run 'RESTORE_COMPLETE'
    return
}

# Apply is deliberately single-run. Resume is restricted to a reboot-interrupted,
# clean NotPresent state, retaining the original StateId and before-state record.
$isResume=($Mode -eq 'Resume')
$isNativeRetry=($Mode -eq 'RetryNative')
$isRecovery=($isResume -or $isNativeRetry)
if($isRecovery){
    Assert-True (Test-Path -LiteralPath $StatePath -PathType Leaf) 'No saved setup state exists.'
    Assert-True (Test-Path -LiteralPath $BeforePath -PathType Leaf) 'Original before-state record is missing.'
    $state=Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $originalBefore=Get-Content -LiteralPath $BeforePath -Raw | ConvertFrom-Json
    if($isNativeRetry){
        $retryEvidencePath=Join-Path $PSScriptRoot 'Recovery-Evidence\terminal-failure-20261029.json'
        $retryResultPath=Join-Path $PSScriptRoot 'Logs\Resume-20261029T204355-aed05ab056-RESULT.json'
        Assert-LocalPath $retryEvidencePath;Assert-LocalPath $retryResultPath
        Assert-True ((File-Hash $retryEvidencePath) -ceq '5dbfc7569620074b88520aca777d3661a8982f59c06e051bc1720e07e7c7f5e5') 'Reviewed terminal evidence changed.'
        Assert-True ((File-Hash $retryResultPath) -ceq 'b95f3e8b73eb9a98f8f4878ab962a6966c3443f00869556e0ec0bb2960ce50d4') 'Reviewed terminal result changed.'
        Assert-True ((File-Hash $StatePath) -ceq '19afc650f234fd6738963f709aec52833088f00dd925e02554a10656a2e62635') 'Failed setup state changed; a new review is required.'
        $retryEvidence=Get-Content -LiteralPath $retryEvidencePath -Raw|ConvertFrom-Json
        $retryPreviousResult=Get-Content -LiteralPath $retryResultPath -Raw|ConvertFrom-Json
    }
    $resumeFacts=Resume-SystemFacts
    Assert-True (@($resumeFacts.NativeProcesses).Count -eq 0 -and -not $resumeFacts.WindowsUpdateBusy) 'Native servicing is active; no capability query or resume was attempted.'
    $readOnlyQueryStarted=[DateTime]::UtcNow
    $current=Snapshot-Current
    [IO.File]::WriteAllText($script:RunSnapshotPath,($current|ConvertTo-Json -Depth 16),$Utf8)
    $resumeFacts=Resume-SystemFacts -QueryParentId $PID -QueryStartedUtc $readOnlyQueryStarted
    if($isNativeRetry){$resumeFacts=Add-RetryNativeFacts $resumeFacts}
    [IO.File]::WriteAllText((Join-Path $logDirectory ($Mode+'-'+$runTag+'-SYSTEM.json')),($resumeFacts|ConvertTo-Json -Depth 8),$Utf8)
    if($isNativeRetry){Assert-RetryNativePreconditions $state $originalBefore $current $resumeFacts $retryPreviousResult $retryEvidence $ConfirmStateId}
    else{Assert-ResumePreconditions $state $originalBefore $current $resumeFacts $ConfirmStateId}
    $preservedState=Join-Path $StateDirectory ('SETUP-STATE-before-'+$Mode.ToLowerInvariant()+'-'+$runTag+'.json')
    [IO.File]::Copy($StatePath,$preservedState,$false)
    if($isNativeRetry){
        if($null -eq $state.PSObject.Properties['NativeRetryHistory']){$state|Add-Member -NotePropertyName NativeRetryHistory -NotePropertyValue @()}
        $state.NativeRetryHistory=@([pscustomobject]@{Attempt=1;StartedUtc=[DateTime]::UtcNow.ToString('o');PreviousStatus=$state.Status;PreviousFailure=$state.Failure;PreviousStateCopy=$preservedState;PriorResult=$retryResultPath;FailureEvidence=$retryEvidencePath;Snapshot=$script:RunSnapshotPath;Reason='One reviewed standard native retry after terminal0x800700b7 and verified Windows cleanup of its failed staging; not a proven fix.'})
        $state.Status='RETRY_NATIVE_ATTEMPT_COMMITTED'
    } else {
        if($null -eq $state.PSObject.Properties['ResumeHistory']){$state|Add-Member -NotePropertyName ResumeHistory -NotePropertyValue @()}
        $state.ResumeHistory=@($state.ResumeHistory)+@([pscustomobject]@{StartedUtc=[DateTime]::UtcNow.ToString('o');BootUtc=$resumeFacts.BootUtc;PreviousStateCopy=$preservedState;Snapshot=$script:RunSnapshotPath;Reason='User restarted MSI after interrupted inbox capability installation; reviewed clean absent state.'})
    }
    $before=$state.Before
    Write-State $state
} else {
Assert-True (-not (Test-Path -LiteralPath $StatePath)) 'Saved setup state already exists. Inspect it; do not rerun installation blindly.'
$before = Snapshot-Current
[IO.File]::WriteAllText($script:RunSnapshotPath,($before | ConvertTo-Json -Depth 16),$Utf8)
Assert-True ($before.CapabilityState -eq 'NotPresent' -and $null -eq $before.Service -and -not $before.ConfigExists) 'Existing/unrecognized OpenSSH installation or config was found; no takeover is performed.'
Assert-True ($null -eq $before.DefaultRule -and $null -eq $before.OwnedRule -and $null -eq $before.BlockRule) 'Existing SSH firewall rule found; review it before proceeding.'
Assert-True ($before.CompetingAllowRules.Count -eq 0) 'Other generic/sshd TCP-22 allow rules exist; review them before proceeding. This helper will not alter unrelated rules.'
Assert-True (@($before.FirewallProfiles | Where-Object { $_.Name -eq 'Private' -and $_.Enabled }).Count -eq 1) 'Private-profile Windows Firewall must already be enabled.'
Assert-True ($before.FirewallProfiles.Count -eq 3 -and @($before.FirewallProfiles | Where-Object { -not $_.Enabled }).Count -eq 0) 'Domain, Private and Public Windows Firewall profiles must all already be enabled. This helper will not enable/disable global firewall profiles.'
Assert-True (@($before.ConnectionProfiles | Where-Object NetworkCategory -eq 'Private').Count -ge 1) 'No current Private network exists. This helper will not change network categories.'
Assert-True (@(Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue).Count -eq 0) 'TCP 22 is already in use.'
if (-not (Test-Path -LiteralPath $StateDirectory)) { New-Item -ItemType Directory -Path $StateDirectory | Out-Null }
Assert-True (-not (Test-Path -LiteralPath $BeforePath)) 'Prior-state record already exists.'
Write-State $before $BeforePath
$state = [ordered]@{ Format='msi-coding-server-setup-v1';StateId=[guid]::NewGuid().ToString();Computer=$ExpectedComputer;OwnerSid=$ExpectedSid;AllowedUser=$AllowedUser;StartedUtc=[DateTime]::UtcNow.ToString('o');Status='PREPARED';Before=$before;CapabilityInstallAttempted=$false;CapabilityInstalledByHelper=$false;InstallRestartNeeded=$false;CreatedDefaultRuleDisabled=$false;ExpectedDefaultRule=$null;CreatedOwnedRule=$false;ExpectedOwnedRule=$null;CreatedBlockRule=$false;ExpectedBlockRule=$null;ConfigSha256=$null;CreatedHostKeyFiles=@();HostKeyFingerprints=@();ServiceStartedByHelper=$false;RestoreCapabilityResult=$null;RestoredUtc=$null;Failure=$null }
Write-State $state
}
try {
    if(-not $isRecovery){
    New-NetFirewallRule -PolicyStore PersistentStore -Name $BlockRule -DisplayName 'MSI coding SSH - block non-private networks' -Description ('Owned by MSI coding setup '+$state.StateId) -Enabled True -Direction Inbound -Action Block -Protocol TCP -LocalPort 22 -RemoteAddress Any -Profile Public,Domain | Out-Null
    $state.CreatedBlockRule=$true;$state.ExpectedBlockRule=Rule-Snapshot $BlockRule;Write-State $state
    }
    if($isNativeRetry){$state.Failure=$null}
    $state.CapabilityInstallAttempted=$true;$state.Status='INSTALLING_CAPABILITY';Write-State $state
    $installation = Add-WindowsCapability -Online -Name $Capability
    $state.CapabilityInstalledByHelper=((Get-WindowsCapability -Online -Name $Capability).State -eq 'Installed')
    $state.InstallRestartNeeded=[bool]$installation.RestartNeeded;Write-State $state
    Disable-NewDefaultRule $state
    Assert-True (-not $state.InstallRestartNeeded) 'Windows requests a restart; review the saved state before continuing. SSH was not started by this helper.'
    Assert-True $state.CapabilityInstalledByHelper 'OpenSSH capability installation did not finish.'
    Assert-OwnedService
    Stop-Service sshd -ErrorAction SilentlyContinue
    Set-Service sshd -StartupType Disabled
    $sshd = Join-Path $OpenSshDirectory 'sshd.exe'
    $keygen = Join-Path $OpenSshDirectory 'ssh-keygen.exe'
    Assert-True ((Test-Path -LiteralPath $sshd -PathType Leaf) -and (Test-Path -LiteralPath $keygen -PathType Leaf)) 'Expected inbox OpenSSH executables missing.'
    if (-not (Test-Path -LiteralPath 'C:\ProgramData\ssh')) { New-Item -ItemType Directory -Path 'C:\ProgramData\ssh' | Out-Null }
    Assert-LocalPath 'C:\ProgramData\ssh'
    Assert-True (-not (Test-Path -LiteralPath $ConfigPath)) 'A configuration appeared during setup; preserve it and review.'
    $config = @'
# MSI coding/data server, prepared 2026-10-29. Allow only the existing local user.
Port 22
AllowUsers jlewi
PasswordAuthentication yes
PubkeyAuthentication yes
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
AllowAgentForwarding no
AllowTcpForwarding local
PermitOpen localhost:* 127.0.0.1:* [::1]:*
GatewayPorts no
UseDNS no
Subsystem sftp sftp-server.exe
Match Group administrators
    AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
'@
    $configBytes=$Utf8.GetBytes($config+"`r`n")
    $stream=[IO.File]::Open($ConfigPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($configBytes,0,$configBytes.Length);$stream.Flush() } finally { $stream.Dispose() }
    Set-NewPrivateFileAcl $ConfigPath
    $state.ConfigSha256=File-Hash $ConfigPath;$state.Status='CONFIGURED_NOT_STARTED';Write-State $state
    $oldHostKeys = @($before.OpenSshFiles | ForEach-Object Name)
    & $keygen -A
    Assert-True ($LASTEXITCODE -eq 0) 'Native host-key generation failed.'
    $createdKeys = @(Get-ChildItem -LiteralPath 'C:\ProgramData\ssh' -File -Filter 'ssh_host_*' | Where-Object { $_.Name -notin $oldHostKeys })
    foreach ($file in $createdKeys) { Set-NewPrivateFileAcl $file.FullName }
    $state.CreatedHostKeyFiles=@($createdKeys | Select-Object Name,Length);Write-State $state
    $fingerprints=@()
    foreach ($publicKey in @(Get-ChildItem -LiteralPath 'C:\ProgramData\ssh' -File -Filter 'ssh_host_*.pub')) {
        $fingerprints+=@(& $keygen -lf $publicKey.FullName)
        Assert-True ($LASTEXITCODE -eq 0) 'Host public-key fingerprint check failed.'
    }
    $state.HostKeyFingerprints=$fingerprints;Write-State $state
    & $sshd -t -f $ConfigPath
    Assert-True ($LASTEXITCODE -eq 0) 'sshd configuration validation failed.'
    # Inbox sshd has service SID type NONE on this host. A service-specific
    # firewall condition cannot match it; retain the exact executable condition.
    New-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule -DisplayName 'MSI coding SSH - private local subnet only' -Description ('Owned by MSI coding setup '+$state.StateId) -Enabled False -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress LocalSubnet -Profile Private -Program $sshd -Service Any | Out-Null
    $state.CreatedOwnedRule=$true;$state.ExpectedOwnedRule=Rule-Snapshot $OwnedRule;Write-State $state
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    $state.ServiceStartedByHelper=$true;Write-State $state
    Enable-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule | Out-Null
    $state.ExpectedOwnedRule=Rule-Snapshot $OwnedRule
    Assert-True (([string]$state.ExpectedOwnedRule.Profile -eq 'Private') -and ($state.ExpectedOwnedRule.RemoteAddress -contains 'LocalSubnet')) 'Scoped firewall rule verification failed.'
    Assert-True ((Get-Service sshd).Status -eq 'Running') 'sshd is not running.'
    $state.Status='SERVER_STARTED_CLIENT_LOGIN_NOT_TESTED';Write-State $state
    Write-Output ('SSH prepared for MSI\jlewi on Private LocalSubnet TCP 22. StateId: '+$state.StateId)
    Write-Output 'A successful new-PC login is not yet verified. From that PC, use ssh jlewi@MSI and enter the account password interactively; this helper never reads it.'
    Complete-Run 'SERVER_STARTED_CLIENT_LOGIN_NOT_TESTED'
} catch {
    $state.Status='FAILED_REVIEW_REQUIRED';$state.Failure=$_.Exception.Message
    # Fail closed only for resources with this helper's absent-server baseline.
    try { Disable-NewDefaultRule $state } catch { }
    try { if ($state.CreatedOwnedRule) { Disable-NetFirewallRule -PolicyStore PersistentStore -Name $OwnedRule | Out-Null;$state.ExpectedOwnedRule=Rule-Snapshot $OwnedRule } } catch { }
    try { if ($null -ne (Service-Snapshot)) { Assert-OwnedService;Stop-Service sshd -Force;Set-Service sshd -StartupType Disabled } } catch { }
    Write-State $state
    throw
}
