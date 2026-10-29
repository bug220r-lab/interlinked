#requires -Version 5.1
<# Read-only server verification, plus an unauthenticated loopback SSH banner.
Writes only its own verification report. Never reads passwords/private keys. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tag=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$reportPath=Join-Path (Join-Path $PSScriptRoot 'Logs') ('VERIFY-'+$tag+'.json')
$report=[ordered]@{Format='msi-coding-server-verification-v1';StartedUtc=[DateTime]::UtcNow.ToString('o');Status='VERIFYING';Checks=[ordered]@{};ConnectionProfiles=@();Banner=$null;HostPublicKeyFingerprints=@();ClientLoginTested=$false;NetworkScopeTest='Local loopback banner only; external LAN/client login not tested';Error=$null}
function Require([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    Require ($env:COMPUTERNAME -ieq 'MSI' -and $identity.User.Value -eq 'S-1-5-21-3086848077-1833151167-136462239-1001') 'Expected existing MSI\jlewi account.'
    $state=Get-Content -LiteralPath (Join-Path (Join-Path $PSScriptRoot 'State') 'SETUP-STATE.json') -Raw | ConvertFrom-Json
    Require ($state.Status -eq 'SERVER_STARTED_CLIENT_LOGIN_NOT_TESTED') 'Setup has not reported a started server.'
    $snapshotText=& (Join-Path $PSScriptRoot 'Set-Up-Coding-Server.ps1') -Mode Inspect
    $snapshot=($snapshotText -join "`n") | ConvertFrom-Json
    $report.ConnectionProfiles=@($snapshot.ConnectionProfiles)
    $report.Checks.EthernetPrivate=(@($snapshot.ConnectionProfiles|Where-Object {$_.InterfaceAlias -eq 'Ethernet' -and [string]$_.NetworkCategory -in @('1','Private')}).Count -eq 1)
    $report.Checks.WiFiPrivate=(@($snapshot.ConnectionProfiles|Where-Object {$_.InterfaceAlias -eq 'Wi-Fi' -and [string]$_.NetworkCategory -in @('1','Private')}).Count -eq 1)
    $report.Checks.CapabilityInstalled=($snapshot.CapabilityState -eq 'Installed')
    $report.Checks.ServiceRunning=($snapshot.Service.Status -eq 'Running')
    $report.Checks.AutomaticStartup=($snapshot.Service.StartType -eq 'Automatic')
    $report.Checks.ConfigHashMatches=($snapshot.ConfigSha256 -eq $state.ConfigSha256)
    $report.Checks.OwnedRuleMatchesSetup=(($snapshot.OwnedRule|ConvertTo-Json -Depth 8 -Compress) -eq ($state.ExpectedOwnedRule|ConvertTo-Json -Depth 8 -Compress))
    $report.Checks.BlockRuleMatchesSetup=(($snapshot.BlockRule|ConvertTo-Json -Depth 8 -Compress) -eq ($state.ExpectedBlockRule|ConvertTo-Json -Depth 8 -Compress))
    $report.Checks.PrivateLocalSubnetRule=($snapshot.OwnedRule.Enabled -eq 'True' -and $snapshot.OwnedRule.Action -eq 'Allow' -and $snapshot.OwnedRule.Profile -eq 'Private' -and $snapshot.OwnedRule.RemoteAddress -contains 'LocalSubnet' -and $snapshot.OwnedRule.LocalPort -contains '22' -and $snapshot.OwnedRule.Service -eq 'Any' -and $snapshot.OwnedRule.Program -ieq 'C:\Windows\System32\OpenSSH\sshd.exe')
    # A service SID of NONE cannot match Service=sshd. The reviewed rule uses
    # the exact executable instead; never mistake rule existence for matching.
    $report.Checks.NoUnsupportedServiceFilter=($snapshot.OwnedRule.Service -eq 'Any')
    $service=Get-CimInstance Win32_Service -Filter "Name='sshd'"
    $report['Listeners']=@(Get-NetTCPConnection -LocalPort 22 -State Listen|Select-Object LocalAddress,LocalPort,OwningProcess)
    $report.Checks.ServiceOwnsIPv4Listener=(@($report.Listeners|Where-Object {$_.LocalAddress -eq '0.0.0.0' -and $_.OwningProcess -eq $service.ProcessId}).Count -eq 1)
    $report.Checks.NonPrivateBlock=($snapshot.BlockRule.Enabled -eq 'True' -and $snapshot.BlockRule.Action -eq 'Block' -and $snapshot.BlockRule.Profile -match 'Public' -and $snapshot.BlockRule.Profile -match 'Domain' -and $snapshot.BlockRule.LocalPort -contains '22')
    $report.Checks.DefaultBroadRuleDisabled=($null -eq $snapshot.DefaultRule -or $snapshot.DefaultRule.Enabled -eq 'False')
    $report.Checks.DefaultRuleMatchesSetup=(($snapshot.DefaultRule|ConvertTo-Json -Depth 8 -Compress) -eq ($state.ExpectedDefaultRule|ConvertTo-Json -Depth 8 -Compress))
    $report.Checks.AllFirewallProfilesEnabled=(@($snapshot.FirewallProfiles | Where-Object {-not $_.Enabled}).Count -eq 0)
    $unexpected=@($snapshot.CompetingAllowRules | Where-Object Name -ne 'Codex-MSI-Coding-SSH-Private-LocalSubnet-20261029')
    $report.Checks.NoUnrelatedDesktopSshAllowRules=($unexpected.Count -eq 0)
    foreach($check in $report.Checks.GetEnumerator()){Require ([bool]$check.Value) ('Verification failed: '+$check.Key)}
    $config=Get-Content -LiteralPath 'C:\ProgramData\ssh\sshd_config' -Raw
    Require ($config -match '(?m)^AllowUsers jlewi\s*$') 'AllowUsers does not match the intended user.'
    $report.Checks.AllowedUserMatches=$true
    $fingerprints=@()
    foreach($key in @(Get-ChildItem -LiteralPath 'C:\ProgramData\ssh' -File -Filter 'ssh_host_*.pub')){
        $fingerprints+=@(& 'C:\Windows\System32\OpenSSH\ssh-keygen.exe' -lf $key.FullName)
        Require ($LASTEXITCODE -eq 0) 'Public-key fingerprint failed.'
    }
    $report.HostPublicKeyFingerprints=$fingerprints
    $report.Checks.PublicFingerprintsMatchSetup=(@(Compare-Object @($state.HostKeyFingerprints | Sort-Object) @($fingerprints | Sort-Object)).Count -eq 0)
    Require $report.Checks.PublicFingerprintsMatchSetup 'Host public fingerprints changed since setup.'
    $client=New-Object Net.Sockets.TcpClient
    try {
        $pending=$client.BeginConnect('127.0.0.1',22,$null,$null)
        Require ($pending.AsyncWaitHandle.WaitOne(5000)) 'Loopback SSH connection timed out.'
        $client.EndConnect($pending);$stream=$client.GetStream();$stream.ReadTimeout=5000
        $bytes=New-Object byte[] 512;$count=$stream.Read($bytes,0,$bytes.Length)
        $report.Banner=[Text.Encoding]::ASCII.GetString($bytes,0,$count).Trim()
        Require ($report.Banner -like 'SSH-2.0-*') 'Expected SSH protocol banner not received.'
        $report.Checks.LocalSshBanner=$true
    } finally {$client.Dispose()}
    $report.Status='LOCAL_SERVER_VERIFIED_CLIENT_LOGIN_PENDING'
} catch {$report.Status='VERIFICATION_FAILED';$report.Error=$_.Exception.Message}
$report.FinishedUtc=[DateTime]::UtcNow.ToString('o')
[IO.File]::WriteAllText($reportPath,($report|ConvertTo-Json -Depth 10),(New-Object Text.UTF8Encoding($false)))
$report|ConvertTo-Json -Depth 10
if($report.Status -eq 'VERIFICATION_FAILED'){exit 1}
