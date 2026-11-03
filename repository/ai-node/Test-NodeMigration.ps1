# Isolated synthetic packages only; does not start a node or inference runtime.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) ('node-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
$checks=New-Object System.Collections.ArrayList
$migration=Join-Path $PSScriptRoot 'Migrate-Node.ps1'
$members=@('interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_stats_process.py','interlinked_configuration.py','Initialize-Node.ps1','Start-Node.ps1','Set-NodeNetwork.ps1','Set-TransportRuntime.ps1','README.md')
$history=@('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py','Migrate-Node.ps1')
function Digest([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Put([string]$Path,[string]$Text){[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path));[IO.File]::WriteAllText($Path,$Text,[Text.UTF8Encoding]::new($false))}
function Json([string]$Path,$Value){Put $Path ($Value|ConvertTo-Json -Depth 20)}
function Check([bool]$Value,[string]$Label){if(-not $Value){throw ('FAILED: '+$Label)};[void]$checks.Add($Label)}
function Reject([scriptblock]$Action,[string]$Label,[string]$Message=''){$errorText=$null;try{& $Action|Out-Null}catch{$errorText=$_.Exception.Message};Check ($null -ne $errorText -and (-not $Message -or $errorText.Contains($Message))) $Label}
function Seal([string]$Path,[string[]]$Names,[string]$Version){$rows=@();foreach($name in $Names){$rows+=@{path=$name;sha256=Digest (Join-Path $Path $name)}};Json (Join-Path $Path 'NODE-CHECKSUMS.json') @{format=$Version;files=$rows}}
function Fixture {
    $dir=Join-Path $root ([guid]::NewGuid().ToString('N'));$install=Join-Path $dir 'install';$candidate=Join-Path $dir 'candidate';$receipt=Join-Path $dir 'receipts'
    foreach($path in @($install,$candidate,$receipt)){[void][IO.Directory]::CreateDirectory($path)}
    foreach($name in $members){Put (Join-Path $install $name) ('old synthetic '+$name)}
    foreach($name in ($members+$history)){Put (Join-Path $candidate $name) ('new synthetic '+$name)}
    Seal $install $members 'interlinked-ai-node-package-v1';Seal $candidate ($members+$history) 'interlinked-ai-node-package-v2'
    Put (Join-Path $install 'Secrets/node-key.pem') 'SYNTHETIC-KEY';Put (Join-Path $install 'Secrets/node-cert.pem') 'SYNTHETIC-CERT'
    Put (Join-Path $install 'State/retained-private-state') 'SYNTHETIC-STATE'
    Put (Join-Path $install '.node.lock') '';Put (Join-Path $install '.ai-node-instance.lock') '0'
    Json (Join-Path $install 'inference-policy.json') @{fixture='retained policy'};Json (Join-Path $install 'reviewed-checks.json') @{fixture=@('retained checks')}
    $python=(Get-Command python).Source
    $record=[ordered]@{format='interlinked-ai-node-install-v1';computer=$env:COMPUTERNAME;sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;python=$python;pythonSha256=Digest $python;manifestSha256=Digest (Join-Path $install 'NODE-CHECKSUMS.json');bind='127.0.0.1';port=47443;fingerprint=('a'*64);startup=$true;inferencePolicySha256=Digest (Join-Path $install 'inference-policy.json');fixtureProperty='preserved'}
    Json (Join-Path $install 'node.json') $record
    [pscustomobject]@{Install=$install;Candidate=$candidate;Receipts=$receipt;Before=$record.manifestSha256;After=Digest (Join-Path $candidate 'NODE-CHECKSUMS.json')}
}
function Invoke($F,[switch]$Apply){& $migration -InstallDirectory $F.Install -CandidateDirectory $F.Candidate -ReceiptDirectory $F.Receipts -ExpectedBeforeManifestSha256 $F.Before -ExpectedAfterManifestSha256 $F.After -Apply:$Apply}
$f=Fixture;$config=Digest (Join-Path $f.Install 'node.json');$result=Invoke $f
Check ($result.status -ceq 'PREFLIGHT_PASSED' -and $result.files -eq 15) 'Exact v1 to v2 preflight passes'
Check ((Digest (Join-Path $f.Install 'node.json')) -ceq $config -and -not(Test-Path (Join-Path $f.Install $history[0]))) 'Preflight leaves installed payload and identity untouched'
$result=Invoke $f -Apply
Check ($result.status -ceq 'NODE_MIGRATED_HASH_VERIFIED') 'Stopped synthetic v1 package migrates to v2'
$record=Get-Content (Join-Path $f.Install 'node.json') -Raw|ConvertFrom-Json
Check ($record.manifestSha256 -ceq $f.After -and $record.fixtureProperty -ceq 'preserved' -and $record.startup -eq $true -and $record.port -eq 47443) 'Only manifest pin changes in installation record'
foreach($entry in (Get-Content (Join-Path $f.Candidate 'NODE-CHECKSUMS.json') -Raw|ConvertFrom-Json).files){Check ((Digest (Join-Path $f.Install $entry.path)) -ceq $entry.sha256) ('Installed member readback: '+$entry.path)}
Check ((Get-Content (Join-Path $f.Install 'State/retained-private-state') -Raw) -ceq 'SYNTHETIC-STATE') 'Private node state is retained'
Check ((Digest (Join-Path (Split-Path $result.receipt) 'before/node.json')) -ceq $config) 'Original installation record is preserved exactly'
Reject {Invoke $f -Apply} 'Old migration pins cannot be replayed'
$f.Before=$f.After;$result=Invoke $f
Check ($result.status -ceq 'PREFLIGHT_PASSED') 'Exact current v2 package remains a supported baseline'
foreach($name in @('.node.lock','.ai-node-instance.lock')){
    $f=Fixture;$handle=[IO.File]::Open((Join-Path $f.Install $name),[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try{Reject {Invoke $f -Apply} ('Held node instance lock refuses migration: '+$name)}finally{$handle.Dispose()}
    Check ((Digest (Join-Path $f.Install 'NODE-CHECKSUMS.json')) -ceq $f.Before) 'Held lock leaves baseline intact'
}
$f=Fixture;$f.Before='0'*64;Reject {Invoke $f -Apply} 'Wrong baseline pin is refused'
$f=Fixture;$f.After='0'*64;Reject {Invoke $f -Apply} 'Wrong candidate pin is refused'
$f=Fixture;Put (Join-Path $f.Install 'interlinked_ai_node.py') 'tampered';Reject {Invoke $f -Apply} 'Tampered baseline member is refused'
$f=Fixture;Put (Join-Path $f.Candidate 'interlinked_history_store.py') 'tampered';Reject {Invoke $f -Apply} 'Tampered candidate history member is refused'
$f=Fixture;Put (Join-Path $f.Install 'inference-policy.json') 'tampered';Reject {Invoke $f -Apply} 'Changed pinned inference policy is refused'
$f=Fixture;Put (Join-Path $f.Install 'interlinked_history_store.py') 'unowned';Reject {Invoke $f -Apply} 'Unowned new destination is preserved'
$f=Fixture;$record=Get-Content (Join-Path $f.Install 'node.json') -Raw|ConvertFrom-Json;$record.computer='OTHER-HOST';Json (Join-Path $f.Install 'node.json') $record;Reject {Invoke $f -Apply} 'Other machine identity is refused'
foreach($names in @(@($members+$history|Select-Object -Skip 1),@($members+$history+$history[0]))){
    $f=Fixture;Seal $f.Candidate $names 'interlinked-ai-node-package-v2';$f.After=Digest (Join-Path $f.Candidate 'NODE-CHECKSUMS.json')
    Reject {Invoke $f -Apply} 'Incomplete or duplicate exact member sets are refused' 'member set'
}
$f=Fixture;Seal $f.Candidate $members 'interlinked-ai-node-package-v1';$f.After=Digest (Join-Path $f.Candidate 'NODE-CHECKSUMS.json');Reject {Invoke $f -Apply} 'Candidate cannot downgrade to v1'
$f=Fixture;$config=Digest (Join-Path $f.Install 'node.json')
$handle=[IO.File]::Open((Join-Path $f.Install 'node.json'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{Reject {Invoke $f -Apply} 'Late replacement failure is surfaced'}finally{$handle.Dispose()}
Check ((Digest (Join-Path $f.Install 'node.json')) -ceq $config -and (Digest (Join-Path $f.Install 'NODE-CHECKSUMS.json')) -ceq $f.Before -and -not(Test-Path (Join-Path $f.Install $history[0]))) 'Failed migration restores original package and config'
$receipt=Get-ChildItem -LiteralPath $f.Receipts -Filter receipt.json -Recurse|Select-Object -First 1
Check ((Get-Content $receipt.FullName -Raw|ConvertFrom-Json).status -ceq 'FAILED_ROLLED_BACK') 'Rollback receipt records truthful failure'
$f=Fixture;$path=Join-Path $f.Install 'interlinked_ai_node.py';New-Item -ItemType HardLink -Path (Join-Path $f.Install 'alias.py') -Target $path|Out-Null
Reject {Invoke $f -Apply} 'Hardlinked installed member is refused'
$package=Join-Path $root 'built-package';$null=& (Join-Path $PSScriptRoot 'Build-Node.ps1') -OutputDirectory $package
$seal=Get-Content (Join-Path $package 'NODE-CHECKSUMS.json') -Raw|ConvertFrom-Json
Check ($seal.format -ceq 'interlinked-ai-node-package-v2' -and $seal.files.Count -eq 15) 'Builder emits exact v2 package with five history modules'
foreach($name in @('Start-Node.ps1','Initialize-Node.ps1')){
    $f=Fixture;Seal $f.Install @($members|Select-Object -Skip 1) 'interlinked-ai-node-package-v1'
    $record=Get-Content (Join-Path $f.Install 'node.json') -Raw|ConvertFrom-Json;$record.manifestSha256=Digest (Join-Path $f.Install 'NODE-CHECKSUMS.json');Json (Join-Path $f.Install 'node.json') $record
    if($name -ceq 'Start-Node.ps1'){Reject {& (Join-Path $PSScriptRoot $name) -InstallDirectory $f.Install} 'Startup rejects incomplete pinned package before process launch' 'member set'}
    else{Reject {& (Join-Path $PSScriptRoot $name) -InstallDirectory $f.Install -ManifestSha256 $record.manifestSha256 -Python $record.python -PythonSha256 $record.pythonSha256} 'Initialization rejects incomplete pinned package before setup' 'member set'}
}
Json (Join-Path $root 'node-migration-test-results.json') @{status='PASS';checks=@($checks);count=$checks.Count;scope='Synthetic stopped packages only; no production node, runtime, network or task changes.'}
[pscustomobject]@{status='PASS';checks=$checks.Count;root=$root}|ConvertTo-Json
