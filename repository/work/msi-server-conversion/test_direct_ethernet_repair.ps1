$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$base=Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'outputs\Project-Library-2026-10-29\Server-Setup'
$results=New-Object Collections.Generic.List[object]
function Import-Function([string]$File,[string]$Name){
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($File,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw 'Parse errors'}
    $fn=$ast.FindAll({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object Name -eq $Name
    if(@($fn).Count -ne 1){throw "Function missing $Name"}
    $text=$fn.Extent.Text -replace ('^function '+[regex]::Escape($Name)+'\b'),('function global:'+$Name)
    Invoke-Expression $text
}
function Reset-Fixture {
    $global:fixtureAdapter=[pscustomobject]@{InterfaceGuid='{2A683B7E-1B94-4858-97C4-48823A0F2AAB}';Name='Ethernet';Status='Up';ifIndex=6}
    $global:fixtureIPs=@([pscustomobject]@{IPAddress='192.168.247.1';PrefixLength=24;PrefixOrigin='Manual';AddressState='Preferred'})
    $global:fixtureDhcp='Disabled'
    $global:fixtureRoutes=@([pscustomobject]@{DestinationPrefix='192.168.247.0/24';NextHop='0.0.0.0'})
    $global:fixtureProfiles=@([pscustomobject]@{NetworkCategory='Public'})
}
function global:Get-NetAdapter {$global:fixtureAdapter}
function global:Get-NetIPAddress {param($InterfaceIndex,$AddressFamily)$global:fixtureIPs}
function global:Get-NetIPInterface {param($InterfaceIndex,$AddressFamily)[pscustomobject]@{Dhcp=$global:fixtureDhcp}}
function global:Get-NetRoute {param($InterfaceIndex)$global:fixtureRoutes}
function global:Get-NetConnectionProfile {param($InterfaceIndex)$global:fixtureProfiles}
function Check([string]$TestName,[scriptblock]$Body){try{& $Body;$results.Add([pscustomobject]@{Name=$TestName;Pass=$true})}catch{$results.Add([pscustomobject]@{Name=$TestName;Pass=$false;Error=$_.Exception.Message})}}
function Must-Fail([scriptblock]$Body){$failed=$false;try{& $Body|Out-Null}catch{$failed=$true};if(-not $failed){throw 'Expected refusal did not occur'}}
$global:guid=[guid]'2A683B7E-1B94-4858-97C4-48823A0F2AAB'
$global:ExpectedGuid=$guid
foreach($name in @('Restore-Trusted-Direct-Ethernet-Now.ps1','Repair-Trusted-Direct-Ethernet.ps1','Set-Up-Direct-Ethernet-Repair.ps1')){
    Check ('PS5 parse '+$name) {$tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $base $name),[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors.Message -join ';')}}
}
Import-Function (Join-Path $base 'Restore-Trusted-Direct-Ethernet-Now.ps1') 'Read-ExactCable'
Import-Function (Join-Path $base 'Repair-Trusted-Direct-Ethernet.ps1') 'Get-DirectLink'
foreach($reader in @('Read-ExactCable','Get-DirectLink')){
    Check ($reader+' accepts exact Public link') {Reset-Fixture;$r=& $reader;if($r.Profile.NetworkCategory -ne 'Public'){throw 'Wrong profile'}}
    Check ($reader+' refuses different GUID') {Reset-Fixture;$fixtureAdapter.InterfaceGuid=[guid]::NewGuid();Must-Fail {& $reader}}
    Check ($reader+' refuses renamed adapter') {Reset-Fixture;$fixtureAdapter.Name='Ethernet 2';Must-Fail {& $reader}}
    Check ($reader+' refuses additional IPv4') {Reset-Fixture;$global:fixtureIPs+=([pscustomobject]@{IPAddress='10.0.0.4';PrefixLength=24;PrefixOrigin='Manual';AddressState='Preferred'});Must-Fail {& $reader}}
    Check ($reader+' refuses DHCP') {Reset-Fixture;$global:fixtureDhcp='Enabled';Must-Fail {& $reader}}
    Check ($reader+' refuses IPv4 gateway') {Reset-Fixture;$global:fixtureRoutes+=([pscustomobject]@{DestinationPrefix='0.0.0.0/0';NextHop='192.168.247.2'});Must-Fail {& $reader}}
    Check ($reader+' refuses IPv6 gateway') {Reset-Fixture;$global:fixtureRoutes+=([pscustomobject]@{DestinationPrefix='::/0';NextHop='fe80::1'});Must-Fail {& $reader}}
    Check ($reader+' refuses domain') {Reset-Fixture;$fixtureProfiles[0].NetworkCategory='DomainAuthenticated';Must-Fail {& $reader}}
}
Check 'Worker waits when cable down' {Reset-Fixture;$fixtureAdapter.Status='Disconnected';if((Get-DirectLink).Ready){throw 'Unexpected ready'}}
Check 'Worker waits when profile unavailable' {Reset-Fixture;$global:fixtureProfiles=@();if((Get-DirectLink).Ready){throw 'Unexpected ready'}}
Check 'One-time refuses cable down' {Reset-Fixture;$fixtureAdapter.Status='Disconnected';Must-Fail {Read-ExactCable}}
Import-Function (Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1') 'Assert-Task'
Import-Function (Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1') 'Resolve-TaskSid'
$global:ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$global:Root='C:\ProgramData\MSI-Coding-Direct-Ethernet-20261029'
$global:TaskName='MSI-Coding-Trusted-Direct-Ethernet-20261029'
$global:PowerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$global:Arguments='EXPECTED'
$owner=[pscustomobject]@{SetupId='fixture'}
function Make-Task {[pscustomobject]@{TaskName=$TaskName;TaskPath='\';Description='Owned MSI direct Ethernet repair fixture';Actions=@([pscustomobject]@{Execute=$PowerShell;Arguments=$Arguments;WorkingDirectory=$Root});Principal=[pscustomobject]@{UserId=$ExpectedSid;LogonType='Interactive';RunLevel='Highest'};Triggers=@([pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'};UserId=$ExpectedSid})}}
Check 'Accept exact owned task' {Assert-Task (Make-Task) $owner}
Check 'Normalize actual short task principal name to SID' {$t=Make-Task;$t.Principal.UserId='jlewi';Assert-Task $t $owner}
Check 'Normalize actual qualified logon trigger to SID' {$t=Make-Task;$t.Triggers[0].UserId='MSI\jlewi';Assert-Task $t $owner}
Check 'Reject normalized different account' {$t=Make-Task;$t.Principal.UserId='SYSTEM';Must-Fail {Assert-Task $t $owner}}
Check 'Refuse altered task command' {$t=Make-Task;$t.Actions[0].Arguments='BAD';Must-Fail {Assert-Task $t $owner}}
Check 'Refuse other task principal' {$t=Make-Task;$t.Principal.UserId='S-1-5-18';Must-Fail {Assert-Task $t $owner}}
Check 'Refuse other logon trigger' {$t=Make-Task;$t.Triggers[0].UserId='OTHER';Must-Fail {Assert-Task $t $owner}}
Check 'Task SDDL parses and denies normal user write' {
    $s=New-Object Security.AccessControl.RawSecurityDescriptor(('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$ExpectedSid+')'))
    $ace=@($s.DiscretionaryAcl|Where-Object {$_.SecurityIdentifier.Value -eq $ExpectedSid})
    if($ace.Count -ne 1 -or ($ace[0].AccessMask -band 0x40000000)){throw 'Unexpected write permission'}
}
Import-Function (Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1') 'Assert-ProtectedSddl'
Check 'Protected ACL accepts exact admin/System plus user read-execute' {Assert-ProtectedSddl ('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$ExpectedSid+')')}
Check 'Protected ACL refuses user write' {Must-Fail {Assert-ProtectedSddl ('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;'+$ExpectedSid+')')}}
Check 'Protected ACL refuses Everyone' {Must-Fail {Assert-ProtectedSddl ('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$ExpectedSid+')(A;;GR;;;WD)')}}
Check 'Protected ACL refuses inherited/unprotected DACL' {Must-Fail {Assert-ProtectedSddl ('O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$ExpectedSid+')')}}
Import-Function (Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1') 'New-Json'
Import-Function (Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1') 'Save-Owner'
Check 'PS5 atomic owner replacement preserves intended JSON' {
    $global:Root=Join-Path $PSScriptRoot ('owner-write-fixture-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Root|Out-Null
    $global:OwnerPath=Join-Path $Root 'OWNER.json'
    New-Json $OwnerPath ([pscustomobject]@{SetupId='fixture';Status='PREPARING'})
    Save-Owner ([pscustomobject]@{SetupId='fixture';Status='INSTALLED'})
    $read=Get-Content -LiteralPath $OwnerPath -Raw|ConvertFrom-Json
    if($read.SetupId -ne 'fixture' -or $read.Status -ne 'INSTALLED'){throw 'Wrong readback'}
}
$payload=[pscustomobject]@{CapturedUtc=[DateTime]::UtcNow.ToString('o');PowerShellVersion=$PSVersionTable.PSVersion.ToString();Scope='Synthetic read-only function/AST tests. No native networking, settings, tasks, or ProgramData writes.';Tests=@($results.ToArray());Passed=@($results|Where-Object Pass).Count;Failed=@($results|Where-Object {-not $_.Pass}).Count;Hashes=@(Get-FileHash -LiteralPath (Join-Path $base 'Restore-Trusted-Direct-Ethernet-Now.ps1'),(Join-Path $base 'Set-Up-Direct-Ethernet-Repair.ps1'),(Join-Path $base 'Repair-Trusted-Direct-Ethernet.ps1') -Algorithm SHA256|Select-Object Path,Hash)}
$payload|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $PSScriptRoot 'direct-ethernet-repair-tests.json') -Encoding UTF8
$payload|Select-Object PowerShellVersion,Passed,Failed|ConvertTo-Json
if($payload.Failed){$results|Where-Object {-not $_.Pass}|ConvertTo-Json;exit 1}
