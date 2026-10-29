# Pure functions/fixture validation: no setup invocation, elevation or Windows
# servicing calls. The known failed records and fresh read-only snapshot are input.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root='C:\Users\jlewi\Documents\Codex\2026-10-28\prep'
$server=Join-Path $root 'outputs\Project-Library-2026-10-29\Server-Setup'
$scriptPath=Join-Path $server 'Set-Up-Coding-Server.ps1'
$tokens=$null;$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$parseErrors)
if(@($parseErrors).Count){throw ($parseErrors|Out-String)}
foreach($name in @('Assert-True','Same-Rule','Assert-CleanNativeRecovery','Assert-RetryNativePreconditions')){
    $nodes=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name)
    if($nodes.Count -ne 1){throw ('Expected one function: '+$name)}
    . ([scriptblock]::Create($nodes[0].Extent.Text))
}
$ExpectedComputer='MSI';$ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001';$AllowedUser='jlewi'
$StatePath=Join-Path $server 'State\SETUP-STATE.json'
$baseSaved=Get-Content -LiteralPath $StatePath -Raw|ConvertFrom-Json
$originalBefore=Get-Content -LiteralPath (Join-Path $server 'State\SETUP-BEFORE.json') -Raw|ConvertFrom-Json
$probe=Get-Content -LiteralPath (Join-Path $root 'work\msi-server-conversion\ssh-native-retry-readonly-20261029T212638-9e25f890.json') -Raw|ConvertFrom-Json
$baseCurrent=$probe.Snapshot;$baseFacts=$probe.SystemFacts
$baseFacts|Add-Member -NotePropertyName OriginalFailedProcessIds -NotePropertyValue @($probe.OriginalProcessIds)
$baseFacts|Add-Member -NotePropertyName FailedStaging -NotePropertyValue @($probe.FailedStaging)
$baseResult=Get-Content -LiteralPath (Join-Path $server 'Logs\Resume-20261029T204355-aed05ab056-RESULT.json') -Raw|ConvertFrom-Json
$baseEvidence=Get-Content -LiteralPath (Join-Path $server 'Recovery-Evidence\terminal-failure-20261029.json') -Raw|ConvertFrom-Json
$confirmation=$baseSaved.StateId;$results=@()
function Clone($value){$value|ConvertTo-Json -Depth 32|ConvertFrom-Json}
function Test-Rejection([string]$Name,[scriptblock]$Mutation){
    $saved=Clone $baseSaved;$current=Clone $baseCurrent;$facts=Clone $baseFacts;$previous=Clone $baseResult;$evidence=Clone $baseEvidence
    & $Mutation $saved $current $facts $previous $evidence
    $rejected=$false
    try{Assert-RetryNativePreconditions $saved $originalBefore $current $facts $previous $evidence $confirmation}catch{$rejected=$true}
    if(-not $rejected){throw ('Unsafe fixture accepted: '+$Name)}
    $script:results+=('PASS: '+$Name)
}
Assert-RetryNativePreconditions $baseSaved $originalBefore $baseCurrent $baseFacts $baseResult $baseEvidence $confirmation
$results+='PASS: exact terminal failure plus clean fresh native state'
Test-Rejection 'wrong StateId' {param($s,$c,$f,$p,$e) $s.StateId='wrong'}
Test-Rejection 'wrong owner' {param($s,$c,$f,$p,$e) $s.OwnerSid='wrong'}
Test-Rejection 'changed original Before' {param($s,$c,$f,$p,$e) $s.Before.CapabilityState='Installed'}
Test-Rejection 'nonterminal previous attempt' {param($s,$c,$f,$p,$e) $s.Status='INSTALLING_CAPABILITY'}
Test-Rejection 'different failure message' {param($s,$c,$f,$p,$e) $s.Failure='other'}
Test-Rejection 'second retry committed' {param($s,$c,$f,$p,$e) $s|Add-Member -NotePropertyName NativeRetryHistory -NotePropertyValue @([pscustomobject]@{Attempt=1})}
Test-Rejection 'prior installer PID still present or reused' {param($s,$c,$f,$p,$e) $f.OriginalFailedProcessIds=@([pscustomobject]@{ProcessId=4036})}
Test-Rejection 'different result attempt' {param($s,$c,$f,$p,$e) $p.Mode='Apply'}
Test-Rejection 'nonterminal result record' {param($s,$c,$f,$p,$e) $p.Status='PENDING'}
Test-Rejection 'different result state path' {param($s,$c,$f,$p,$e) $p.StatePath='C:\not-our-state.json'}
Test-Rejection 'result completion later than snapshot' {param($s,$c,$f,$p,$e) $f.CapturedUtc='2026-10-29T21:00:00Z'}
Test-Rejection 'different HRESULT evidence' {param($s,$c,$f,$p,$e) $e.TerminalResult.HResult='0x80070002'}
Test-Rejection 'evidence belongs to another StateId' {param($s,$c,$f,$p,$e) $e.StateId='wrong'}
Test-Rejection 'staging destination remains' {param($s,$c,$f,$p,$e) $f.FailedStaging[1].Status='PRESENT'}
Test-Rejection 'staging metadata incomplete' {param($s,$c,$f,$p,$e) $f.FailedStaging=@($f.FailedStaging[0])}
Test-Rejection 'staging absence refers to wrong path' {param($s,$c,$f,$p,$e) $f.FailedStaging[1].Path='C:\other'}
Test-Rejection 'active external DISM' {param($s,$c,$f,$p,$e) $f.NativeProcesses=@([pscustomobject]@{Id=10})}
Test-Rejection 'WUA busy' {param($s,$c,$f,$p,$e) $f.WindowsUpdateBusy=$true}
Test-Rejection 'pending reboot keys' {param($s,$c,$f,$p,$e) $f.PendingServicingKeys=@('fixture')}
Test-Rejection 'WUA reboot required' {param($s,$c,$f,$p,$e) $f.WindowsUpdateRequiresReboot=$true}
Test-Rejection 'new SSH service' {param($s,$c,$f,$p,$e) $c.Service=[pscustomobject]@{Status='Stopped'}}
Test-Rejection 'new SSH config' {param($s,$c,$f,$p,$e) $c.ConfigExists=$true}
Test-Rejection 'new host key metadata' {param($s,$c,$f,$p,$e) $c.OpenSshFiles=@([pscustomobject]@{Name='ssh_host_rsa_key'})}
Test-Rejection 'installed capability' {param($s,$c,$f,$p,$e) $c.CapabilityState='Installed'}
Test-Rejection 'new SSH listener' {param($s,$c,$f,$p,$e) $f.Port22Listeners=@([pscustomobject]@{LocalPort=22})}
Test-Rejection 'new default allow rule' {param($s,$c,$f,$p,$e) $c.DefaultRule=[pscustomobject]@{Name='OpenSSH-Server-In-TCP'}}
Test-Rejection 'block rule changed' {param($s,$c,$f,$p,$e) $c.BlockRule.Enabled='False'}
Test-Rejection 'firewall profile disabled' {param($s,$c,$f,$p,$e) $c.FirewallProfiles[0].Enabled=0}
$report=[ordered]@{Status='PASS';CheckedUtc=[DateTime]::UtcNow.ToString('o');SetupSha256=(Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant();Checks=$results;SetupExecuted=$false;SettingsChanged=$false;PrivateKeysRead=$false}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'ssh-retry-native-guard-tests.json'),($report|ConvertTo-Json -Depth 8),(New-Object Text.UTF8Encoding($false)))
$report|ConvertTo-Json -Depth 8
