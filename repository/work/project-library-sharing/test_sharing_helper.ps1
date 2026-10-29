$ErrorActionPreference='Stop'
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$path=Join-Path $root 'outputs\Project-Library-2026-10-29\Helper\Set-Up-MSI-Sharing.ps1'
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors.Message -join '; ')}
# Load function definitions only. Never run the elevated setup's top-level body.
foreach($fn in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){Invoke-Expression $fn.Extent.Text}
$script:ExpectedComputer='MSI';$script:ExpectedAccount='MSI\jlewi';$script:ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$script:LibraryRoot=Join-Path $root 'outputs\Project-Library-2026-10-29'
$results=New-Object 'System.Collections.Generic.List[object]'
function Test-Case([string]$Name,[scriptblock]$Code){try{&$Code;$results.Add([pscustomobject]@{test=$Name;status='PASS'})}catch{$results.Add([pscustomobject]@{test=$Name;status='FAIL';error=$_.Exception.Message})}}
function Expect-Failure([scriptblock]$Code){$failed=$false;try{&$Code}catch{$failed=$true};if(-not $failed){throw 'Expected refusal did not occur'}}
function Reset-Fixture {
 $owner='11111111111111111111111111111111';$d='Codex Project Library 2026-10-29 owner '+$owner
 $script:State=[pscustomobject]@{Format='msi-project-library-sharing-v1';Computer='MSI';AccountSid=$script:ExpectedSid;LibraryRoot=$script:LibraryRoot;OwnerId=$owner;Status='PLANNED';UpdatedUtc=$null;RestoreProblems=@();Shares=@([pscustomobject]@{Name='ProjectLibrary';Path=(Join-Path $script:LibraryRoot 'HostLibrary');Right='Read';Description=$d;CreationIntent=$true;Created=$true},[pscustomobject]@{Name='ProjectReturns';Path=(Join-Path $script:LibraryRoot 'Returns');Right='Change';Description=$d;CreationIntent=$true;Created=$true});Firewall=[pscustomobject]@{Name=('CodexProjectLibrary-20261029-'+$owner);Description=$d;CreationIntent=$true;Created=$true};Service=[pscustomobject]@{PriorState='Running';PriorStartMode='Auto';StartIntent=$false;StartupChangeIntent=$false;StartedByHelper=$false;StartupChanged=$false}}
 $script:MockShares=@{};$script:RemovedShares=@();$script:RemovedRules=@();$script:MockAccessDrift=$false
 foreach($s in $script:State.Shares){$script:MockShares[$s.Name]=[pscustomobject]@{Name=$s.Name;Path=$s.Path;Description=$s.Description;EncryptData=$true;CachingMode='None'}}
 $script:MockRule=[pscustomobject]@{Name=$script:State.Firewall.Name;Description=$d;Direction='Inbound';Action='Allow';Enabled='True';Profile='Private'}
 $script:MockPort=[pscustomobject]@{Protocol='TCP';LocalPort='445'};$script:MockAddress=[pscustomobject]@{RemoteAddress='LocalSubnet'};$script:MockApplication=[pscustomobject]@{Program='System'}
}
# Every Windows configuration command reachable in these tests is replaced locally.
function Save-State {}
function Find-Share([string]$Name){$script:MockShares[$Name]}
function Find-Rule([string]$Name){if($script:MockRule -and $script:MockRule.Name -eq $Name){$script:MockRule}}
function Get-SmbShareAccess {param($Name,$ErrorAction);$s=$script:State.Shares|Where-Object Name -eq $Name;[pscustomobject]@{AccountName=$(if($script:MockAccessDrift){'S-1-1-0'}else{$script:ExpectedSid});AccessControlType='Allow';AccessRight=$s.Right}}
function Get-NetFirewallPortFilter {param([Parameter(ValueFromPipeline=$true)]$InputObject);process{$script:MockPort}}
function Get-NetFirewallAddressFilter {param([Parameter(ValueFromPipeline=$true)]$InputObject);process{$script:MockAddress}}
function Get-NetFirewallApplicationFilter {param([Parameter(ValueFromPipeline=$true)]$InputObject);process{$script:MockApplication}}
function Remove-SmbShare {param($Name,[switch]$Force,$Confirm);$script:RemovedShares+= $Name;$script:MockShares.Remove($Name)}
function Remove-NetFirewallRule {param([Parameter(ValueFromPipeline=$true)]$InputObject,$Confirm);process{$script:RemovedRules+=$InputObject.Name;$script:MockRule=$null}}
function Get-ServiceEvidence {[pscustomobject]@{Name='LanmanServer';State='Running';StartMode='Auto'}}
function Get-SmbSession {param($ErrorAction);[pscustomobject]@{SessionId=123}}
function Get-SmbShare {[pscustomobject]@{Name='UnrelatedShare';Special=$false}}
function Stop-Service {throw 'Unexpected real service stop path in fixture'}
function Set-Service {throw 'Unexpected real service startup change path in fixture'}

Test-Case 'PowerShell 5.1 AST parse' {if($PSVersionTable.PSVersion.Major -ne 5){throw 'Tests must run under Windows PowerShell 5.1'}}
Test-Case 'Expected ownership receipt accepted' {Reset-Fixture;Assert-State $script:State}
Test-Case 'Foreign principal receipt refused' {Reset-Fixture;$script:State.AccountSid='S-1-1-0';Expect-Failure {Assert-State $script:State}}
Test-Case 'Broad source share root refused' {Reset-Fixture;$script:State.Shares[0].Path='C:\Users';Expect-Failure {Assert-State $script:State}}
Test-Case 'Duplicate share receipt refused' {Reset-Fixture;$script:State.Shares[1]=$script:State.Shares[0];Expect-Failure {Assert-State $script:State}}
Test-Case 'Owner description drift refused' {Reset-Fixture;$script:MockShares.ProjectLibrary.Description='someone else';Expect-Failure {Assert-ShareDefinition $script:MockShares.ProjectLibrary $script:State.Shares[0]}}
Test-Case 'Everyone share access refused' {Reset-Fixture;$script:MockAccessDrift=$true;Expect-Failure {Assert-ShareDefinition $script:MockShares.ProjectLibrary $script:State.Shares[0]}}
Test-Case 'Encryption disabled refused' {Reset-Fixture;$script:MockShares.ProjectLibrary.EncryptData=$false;Expect-Failure {Assert-ShareDefinition $script:MockShares.ProjectLibrary $script:State.Shares[0]}}
Test-Case 'Public firewall profile refused' {Reset-Fixture;$script:MockRule.Profile='Public';Expect-Failure {Assert-RuleDefinition $script:MockRule}}
Test-Case 'Broad firewall remote scope refused' {Reset-Fixture;$script:MockAddress.RemoteAddress='Any';Expect-Failure {Assert-RuleDefinition $script:MockRule}}
Test-Case 'Owned restore touches exact resources only' {Reset-Fixture;Restore-OwnedResources;if(($script:RemovedShares -join ',') -ne 'ProjectLibrary,ProjectReturns' -or $script:RemovedRules.Count -ne 1 -or $script:State.Status -ne 'RESTORED_OWNED_RESOURCES'){throw 'Restore resource mismatch'}}
Test-Case 'Changed share retained during restore' {Reset-Fixture;$script:MockShares.ProjectLibrary.Description='someone else';Expect-Failure {Restore-OwnedResources};if('ProjectLibrary' -in $script:RemovedShares -or $script:State.Status -ne 'RESTORE_PARTIAL_REVIEW_REQUIRED'){throw 'Changed share was removed or partial not reported'}}
Test-Case 'Active sessions prevent helper-started service stop' {Reset-Fixture;$script:State.Service.PriorState='Stopped';$script:State.Service.StartIntent=$true;Expect-Failure {Restore-OwnedResources};if($script:State.Status -ne 'RESTORE_PARTIAL_REVIEW_REQUIRED'){throw 'Expected partial service restore'}}
Test-Case 'Known Windows junction refused without traversal' {Expect-Failure {Assert-PlainPath 'C:\Users\jlewi\Documents\My Music'}}
Test-Case 'Atomic receipt create and replace' {$p=Join-Path $PSScriptRoot ('receipt-fixture-'+[guid]::NewGuid().ToString('N')+'.json');Write-AtomicJson $p @{value=1};Write-AtomicJson $p @{value=2};if((Get-Content -LiteralPath $p -Raw|ConvertFrom-Json).value -ne 2){throw 'Atomic receipt roundtrip failed'}}
Test-Case 'No source ACL/profile/account commands' {$code=Get-Content -LiteralPath $path -Raw;if($code -match '(?i)\b(Set-Acl|icacls|Set-NetConnectionProfile|New-LocalUser|Set-LocalUser|Enable-WindowsOptionalFeature)\b'){throw 'Prohibited configuration command present'}}
$out=[ordered]@{engineSha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();powershell=$PSVersionTable.PSVersion.ToString();tests=@($results.ToArray());passed=@($results|Where-Object status -eq PASS).Count;failed=@($results|Where-Object status -eq FAIL).Count;scope='Mocked Windows configuration functions only; no shares, services, firewall, account or ACL settings changed. Known junction checked by metadata only.'}
$out|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $PSScriptRoot 'test-results.json') -Encoding UTF8
$out|ConvertTo-Json -Depth 8
if($out.failed){exit 1}
