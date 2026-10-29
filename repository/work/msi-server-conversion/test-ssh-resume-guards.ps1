# Pure guard fixtures only: no setup script execution, elevation, installation,
# service/firewall changes or reads of private keys.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root='C:\Users\jlewi\Documents\Codex\2026-10-28\prep'
$server=Join-Path $root 'outputs\Project-Library-2026-10-29\Server-Setup'
$scriptPath=Join-Path $server 'Set-Up-Coding-Server.ps1'
$tokens=$null;$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$parseErrors)
if(@($parseErrors).Count){throw ($parseErrors|Out-String)}
foreach($name in @('Assert-True','Same-Rule','Assert-CleanNativeRecovery','Assert-ResumePreconditions','Classify-ResumeNativeProcesses')){
    $node=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
    if($node.Count -ne 1){throw ('Expected one guard function: '+$name)}
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$ExpectedComputer='MSI';$ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001';$AllowedUser='jlewi'
$baseSaved=Get-Content -LiteralPath (Join-Path $server 'State\SETUP-STATE-before-resume-20261029T204355-aed05ab056.json') -Raw|ConvertFrom-Json
$originalBefore=Get-Content -LiteralPath (Join-Path $server 'State\SETUP-BEFORE.json') -Raw|ConvertFrom-Json
$baseCurrent=Get-Content -LiteralPath (Join-Path $server 'Logs\Inspect-20261029T203932-6fa8077726-SNAPSHOT.json') -Raw|ConvertFrom-Json
$baseFacts=[pscustomobject]@{BootUtc='2026-10-29T20:31:00Z';NativeProcesses=@();PendingServicingKeys=@();WindowsUpdateBusy=$false;WindowsUpdateRequiresReboot=$false;Port22Listeners=@()}
$confirmation=$baseSaved.StateId
$results=@()
$queryStart=[DateTime]::Parse('2026-10-29T20:40:00Z').ToUniversalTime()
$nativeFixture=@(
    [pscustomobject]@{ProcessId=101;Name='DismHost.exe';ParentProcessId=42;CreationDate=$queryStart.AddSeconds(1)},
    [pscustomobject]@{ProcessId=102;Name='DismHost.exe';ParentProcessId=99;CreationDate=$queryStart.AddSeconds(1)},
    [pscustomobject]@{ProcessId=103;Name='DismHost.exe';ParentProcessId=42;CreationDate=$queryStart.AddSeconds(-1)},
    [pscustomobject]@{ProcessId=104;Name='dism.exe';ParentProcessId=42;CreationDate=$queryStart.AddSeconds(1)}
)
$classified=Classify-ResumeNativeProcesses $nativeFixture 42 $queryStart
if(@($classified.OwnReadOnlyQueryHosts).Count -ne 1 -or $classified.OwnReadOnlyQueryHosts[0].Id -ne 101 -or @($classified.External).Count -ne 3){throw 'Native host classification failed.'}
$results+='PASS: only newly spawned direct read-only query host exempted'
$classified=Classify-ResumeNativeProcesses $nativeFixture
if(@($classified.External).Count -ne 4 -or @($classified.OwnReadOnlyQueryHosts).Count -ne 0){throw 'Pre-query native host rejection failed.'}
$results+='PASS: all native hosts block before query provenance exists'
function Clone($value){$value|ConvertTo-Json -Depth 32|ConvertFrom-Json}
function Test-Rejection([string]$Name,[scriptblock]$Mutation){
    $saved=Clone $baseSaved;$current=Clone $baseCurrent;$facts=Clone $baseFacts
    & $Mutation $saved $current $facts
    $rejected=$false
    try{Assert-ResumePreconditions $saved $originalBefore $current $facts $confirmation}catch{$rejected=$true}
    if(-not $rejected){throw ('Unsafe fixture accepted: '+$Name)}
    $script:results+=('PASS: '+$Name)
}
Assert-ResumePreconditions $baseSaved $originalBefore $baseCurrent $baseFacts $confirmation
$results+='PASS: observed clean absent-state reboot recovery'
Test-Rejection 'wrong state identity' {param($s,$c,$f) $s.StateId='wrong'}
Test-Rejection 'wrong owner' {param($s,$c,$f) $s.OwnerSid='wrong'}
Test-Rejection 'setup passed recoverable stage' {param($s,$c,$f) $s.Status='CONFIGURED_NOT_STARTED'}
Test-Rejection 'original provenance mismatch' {param($s,$c,$f) $s.Before.CapabilityState='Installed'}
Test-Rejection 'no later reboot' {param($s,$c,$f) $f.BootUtc='2026-10-29T19:00:00Z'}
Test-Rejection 'resume attempt occurred in this boot' {param($s,$c,$f) $s|Add-Member -NotePropertyName ResumeHistory -NotePropertyValue @([pscustomobject]@{StartedUtc='2026-10-29T20:39:00Z'})}
Test-Rejection 'Resume cannot bypass committed RetryNative' {param($s,$c,$f) $s|Add-Member -NotePropertyName NativeRetryHistory -NotePropertyValue @([pscustomobject]@{Attempt=1})}
Test-Rejection 'active DISM operation' {param($s,$c,$f) $f.NativeProcesses=@([pscustomobject]@{Id=1;ProcessName='DismHost'})}
Test-Rejection 'Windows Update busy' {param($s,$c,$f) $f.WindowsUpdateBusy=$true}
Test-Rejection 'pending servicing key' {param($s,$c,$f) $f.PendingServicingKeys=@('fixture')}
Test-Rejection 'Windows Update reboot needed' {param($s,$c,$f) $f.WindowsUpdateRequiresReboot=$true}
Test-Rejection 'existing listener' {param($s,$c,$f) $f.Port22Listeners=@([pscustomobject]@{LocalPort=22})}
Test-Rejection 'capability unexpectedly installed' {param($s,$c,$f) $c.CapabilityState='Installed'}
Test-Rejection 'unexpected service' {param($s,$c,$f) $c.Service=[pscustomobject]@{Status='Stopped'}}
Test-Rejection 'unexpected configuration' {param($s,$c,$f) $c.ConfigExists=$true}
Test-Rejection 'unexpected host key metadata' {param($s,$c,$f) $c.OpenSshFiles=@([pscustomobject]@{Name='ssh_host_ed25519_key';Length=1})}
Test-Rejection 'unexpected default allow' {param($s,$c,$f) $c.DefaultRule=[pscustomobject]@{Name='OpenSSH-Server-In-TCP'}}
Test-Rejection 'owned block drift' {param($s,$c,$f) $c.BlockRule.Enabled='False'}
Test-Rejection 'competing allow rule' {param($s,$c,$f) $c.CompetingAllowRules=@([pscustomobject]@{Name='unrecognized'})}
Test-Rejection 'disabled firewall profile' {param($s,$c,$f) $c.FirewallProfiles[0].Enabled=0}
Test-Rejection 'no private network' {param($s,$c,$f) foreach($p in $c.ConnectionProfiles){$p.NetworkCategory=0}}
$launcherTokens=$null;$launcherErrors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $server 'Launch-Coding-Server-Setup.ps1'),[ref]$launcherTokens,[ref]$launcherErrors)|Out-Null
if(@($launcherErrors).Count){throw ($launcherErrors|Out-String)}
$report=[ordered]@{Status='PASS';CheckedUtc=[DateTime]::UtcNow.ToString('o');SetupSha256=(Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant();LauncherSha256=(Get-FileHash -LiteralPath (Join-Path $server 'Launch-Coding-Server-Setup.ps1') -Algorithm SHA256).Hash.ToLowerInvariant();Checks=$results;SetupExecuted=$false;SettingsChanged=$false;PrivateKeysRead=$false}
$reportPath=Join-Path $root 'work\msi-server-conversion\ssh-resume-guard-tests.json'
[IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 8),(New-Object Text.UTF8Encoding($false)))
$report|ConvertTo-Json -Depth 8
