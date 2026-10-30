# Isolated synthetic fixtures only. No installed app, media, USB or sensor calls.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$output=[IO.Path]::GetFullPath($OutputDirectory)
$kitRoot=[IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'Documents\MSI-Project-Library')).TrimEnd('\')
$cacheRoot=[IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'AppData\Local\ProjectLibrary\Cache')).TrimEnd('\')
foreach($forbidden in @($kitRoot,$cacheRoot)){if($output.Equals($forbidden,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($forbidden+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Use fixture output outside the production kit/cache.'}}
if(Test-Path -LiteralPath $output){throw 'Use an absent fixture output directory.'}
[void][IO.Directory]::CreateDirectory($output)
$guardPath=Join-Path $PSScriptRoot 'Interlinked-Desktop-Publication.psm1'
$guard=Import-Module $guardPath -Force -PassThru
Import-Module (Join-Path $PSScriptRoot 'Interlinked-Cooler-Publication.psm1') -Force
& $guard {
 $script:FixtureProcesses=@('fixture_editor_closed');$script:GuardReadLocked=$false
 function script:Get-Process {
  param($ErrorAction)
  # Prove the actual guard source remains protected during transaction checks.
  $probe=$null
  try{$probe=[IO.File]::Open($script:FixtureGuardPath,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)}
  catch [IO.IOException]{$script:GuardReadLocked=$true}
  finally{if($probe){$probe.Dispose()}}
  foreach($name in $script:FixtureProcesses){[pscustomobject]@{ProcessName=$name}}
 }
 $script:FixtureGuardPath=$ExecutionContext.SessionState.Module.Path
}
$checks=New-Object System.Collections.ArrayList
function Digest([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Put([string]$Path,[string]$Text){[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path));[IO.File]::WriteAllText($Path,$Text,[Text.UTF8Encoding]::new($false))}
function Json([string]$Path,$Value){Put $Path ($Value|ConvertTo-Json -Depth 16)}
function Check([bool]$Value,[string]$Label){if(-not $Value){throw ('FAILED: '+$Label)};[void]$checks.Add($Label)}
function Reject([scriptblock]$Action,[string]$Label){$failed=$false;try{& $Action|Out-Null}catch{$failed=$true};Check $failed $Label}
$allowed=@('INTERLINKED.cmd','INTERLINKED-Browser.cmd','INTERLINKED-App/interlinked_gui.py','INTERLINKED-App/GUI-CHECKSUMS.json',
 'INTERLINKED-App/interlinked_controller.py','INTERLINKED-App/interlinked_hub.py','INTERLINKED-App/interlinked_mw5.py',
 'INTERLINKED-App/interlinked_master.py','INTERLINKED-App/master_hook.py','INTERLINKED-App/master_bridge.py',
 'INTERLINKED-App/interlinked_jobs.py','INTERLINKED-App/interlinked_buddy.py',
 'INTERLINKED-App/interlinked_stats.py','INTERLINKED-App/interlinked_stats_process.py','INTERLINKED-App/interlinked_configuration.py',
 'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
 'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config',
 'INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe.config','INTERLINKED-Desktop/Violet.png',
 'INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll','INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll',
 'INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt','INTERLINKED-Desktop/WebView2-NOTICE.txt','INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
$worker='INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe';$sealKey='INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json'
function Fixture {
 param([ValidateSet('v5','v6','v7')][string]$Schema='v7')
 $allowed=@($script:allowed)
 if($Schema-ceq 'v5'){$allowed=@($allowed|Where-Object {$_-cnotin @('INTERLINKED-App/interlinked_stats.py','INTERLINKED-App/interlinked_stats_process.py','INTERLINKED-App/interlinked_configuration.py')})}
 elseif($Schema-ceq 'v6'){$allowed=@($allowed|Where-Object {$_-cne 'INTERLINKED-App/interlinked_configuration.py'})}
 $workspace=Join-Path $output ([guid]::NewGuid().ToString('N'))
 $kit=Join-Path $workspace 'kit';$source=Join-Path $workspace 'source';$receipts=Join-Path $workspace 'receipts'
 foreach($dir in @($kit,$source,$receipts)){[void][IO.Directory]::CreateDirectory($dir)}
 foreach($name in $allowed){Put (Join-Path $kit $name) ('baseline fixture '+$name)}
 Put (Join-Path $kit 'MAGIX-Media-Audit.json') '{"synthetic":true}'
 $guiFiles=[ordered]@{}
 foreach($name in $allowed|Where-Object {$_-like 'INTERLINKED-App/*'-and $_-notlike '*/GUI-CHECKSUMS.json'}){$guiFiles[$name.Substring(16)]=Digest (Join-Path $kit $name)}
 Json (Join-Path $kit 'INTERLINKED-App/GUI-CHECKSUMS.json') @{format=('interlinked-gui-checksums-'+$Schema);files=$guiFiles;auditSha256=Digest (Join-Path $kit 'MAGIX-Media-Audit.json')}
 $desktopFiles=[ordered]@{}
 foreach($name in $allowed|Where-Object {$_-like 'INTERLINKED-Desktop/*'-and $_-cne $sealKey}){$desktopFiles[$name.Substring(20)]=Digest (Join-Path $kit $name)}
 Json (Join-Path $kit $sealKey) @{format='interlinked-desktop-checksums-v1';sdkVersion='1.0.4314.35';sdkArchiveSha256='90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4';createdUtc='2026-10-30T00:00:00Z';files=$desktopFiles}
 foreach($name in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){Put (Join-Path $kit $name) ''}
 foreach($name in @('binding.json','multi-binding.json')){Json (Join-Path $kit ('MAGIX-Workspace-State/'+$name)) @{status='UNBOUND'}}
 $pins=@{};foreach($name in @('magix_projects.py','magix_workspace.py','MAGIX-All-Projects-Index.json')){Put (Join-Path $kit $name) ('pin '+$name);$pins[$name]=Digest (Join-Path $kit $name)}
 $client=Join-Path $kit 'Project-Library-Client';$sealed=@()
 foreach($name in @('project_library.py','Runtime/python.exe')){Put (Join-Path $client $name) ('runtime fixture '+$name);$path=Join-Path $client $name;$sealed+=@{path=$name;bytes=(Get-Item -LiteralPath $path).Length;sha256=Digest $path}}
 Json (Join-Path $client 'HELPER-CHECKSUMS.json') @{format='project-library-client-checksums-v1';files=$sealed}
 foreach($name in $allowed){$destination=Join-Path $source $name;[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination));[IO.File]::Copy((Join-Path $kit $name),$destination)}
 Put (Join-Path $source $worker) 'new synthetic worker; never executed'
 $desktopFiles['INTERLINKED.Master.Worker.exe']=Digest (Join-Path $source $worker)
 Json (Join-Path $source $sealKey) @{format='interlinked-desktop-checksums-v1';sdkVersion='1.0.4314.35';sdkArchiveSha256='90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4';createdUtc='2026-10-30T00:00:01Z';files=$desktopFiles}
 $rows=@();foreach($name in $allowed){$rows+=@{path=$name;source=Join-Path $source $name;sha256=Digest (Join-Path $source $name);beforeSha256=Digest (Join-Path $kit $name)}}
 $plan=Join-Path $workspace 'plan.json';Json $plan @{format='interlinked-desktop-publication-v1';files=$rows}
 $context=[pscustomobject]@{Workspace=$workspace;Kit=$kit;Pins=$pins;ClientFileCount=2;ClientSealSha256=Digest (Join-Path $client 'HELPER-CHECKSUMS.json')}
 [pscustomobject]@{Context=$context;Plan=$plan;Rows=$rows;Source=$source;Receipts=$receipts}
}
function Invoke($F,[switch]$Apply){Invoke-CoolerPublication -Context $F.Context -ManifestPath $F.Plan -ManifestSha256 (Digest $F.Plan) -ReceiptDirectory $F.Receipts -Apply:$Apply}
function Refresh-Plan($F){foreach($row in $F.Rows){$row.sha256=Digest $row.source};Json $F.Plan @{format='interlinked-desktop-publication-v1';files=$F.Rows}}

$f=Fixture;$result=Invoke $f
Check ($result.status-ceq 'PREFLIGHT_PASSED'-and $result.files-eq 0-and $result.verifiedPlanFiles-eq 29) 'Dry-run validates all 29 v7 rows and writes no targets'
foreach($row in $f.Rows){Check ((Digest (Join-Path $f.Context.Kit $row.path))-ceq $row.beforeSha256) ('Dry-run preserves '+$row.path)}
$f=Fixture;$result=Invoke $f -Apply
Check ($result.status-ceq 'COOLER_PUBLISHED_HASH_VERIFIED'-and $result.files-eq 2) 'Valid cooler-only package publishes exactly two changed files'
$receipt=Get-Content -LiteralPath $result.receipt -Raw|ConvertFrom-Json
Check (@($receipt.files).Count-eq 2-and $receipt.files[0].path-ceq $worker-and $receipt.files[1].path-ceq $sealKey) 'Worker publishes before its seal and no other targets'
foreach($row in $f.Rows){Check ((Digest (Join-Path $f.Context.Kit $row.path))-ceq $row.sha256) ('Final readback matches '+$row.path)}
foreach($row in $receipt.files){Check ((Digest $row.backup)-ceq $row.beforeSha256) ('Exact prior bytes retained: '+$row.path)}
Check ($receipt.approvedVioletUnchanged-and $receipt.guiAndLaunchersUnchanged-and -not $receipt.projectFilesWritten-and -not $receipt.workspaceStateWritten) 'Receipt records preserved artwork, GUI, launchers and workspaces'
Check ([bool](& $guard {$script:GuardReadLocked})) 'Shared guard module is read-locked during transaction checks'

foreach($schema in @('v5','v6','v7')){
 $f=Fixture -Schema $schema;$guiBefore=Digest (Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json');$result=Invoke $f -Apply
 $expected=if($schema-ceq 'v5'){26}elseif($schema-ceq 'v6'){28}else{29}
 Check ($result.status-ceq 'COOLER_PUBLISHED_HASH_VERIFIED'-and $result.files-eq 2-and $result.verifiedPlanFiles-eq $expected) ('Complete '+$schema+' baseline preserves its schema with exact plan coverage')
 Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json'))-ceq $guiBefore) ('Cooler publication preserves '+$schema+' GUI seal bytes')
}
foreach($companion in @('interlinked_stats.py','interlinked_stats_process.py','interlinked_configuration.py')){
 $f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files|Where-Object path -cne ('INTERLINKED-App/'+$companion));Json $f.Plan $plan
 Reject {Invoke $f -Apply} ('V7 plan cannot omit sealed companion: '+$companion)
 $f=Fixture;Put (Join-Path $f.Context.Kit ('INTERLINKED-App/'+$companion)) 'tampered sealed companion'
 Reject {Invoke $f -Apply} ('V7 installed companion hash is checked: '+$companion)
}

foreach($lock in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){
 $f=Fixture;$handle=[IO.File]::Open((Join-Path $f.Context.Kit $lock),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
 try{Reject {Invoke $f -Apply} ('Held lock refuses publication: '+$lock)}finally{$handle.Dispose()}
}
foreach($status in @('STARTING','RUNNING','STOPPING','RECOVERY_REQUIRED')){
 $f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/stats/session.json') @{format='interlinked-stats-session-v1';status=$status}
 Reject {Invoke $f -Apply} ('Nonidle Stats refuses publication: '+$status)
}
foreach($name in @('binding.json','multi-binding.json')){$f=Fixture;Json (Join-Path $f.Context.Kit ('MAGIX-Workspace-State/'+$name)) @{status='BOUND'};Reject {Invoke $f -Apply} ('Bound MAGIX journal refuses publication: '+$name)}
foreach($journal in @('mw5/session.json','folder-session.json')){
 $f=Fixture;$format=if($journal.StartsWith('mw5')){'interlinked-mw5-session-v1'}else{'interlinked-folder-session-v1'}
 Json (Join-Path $f.Context.Kit ('INTERLINKED-GUI-State/'+$journal)) @{format=$format;status='RUNNING'}
 Reject {Invoke $f -Apply} ('Active adapter journal refuses publication: '+$journal)
}
foreach($name in @('videodeluxe','UnrealEditor','INTERLINKED','INTERLINKED.Master.Worker')){
 & $guard {param($name)$script:FixtureProcesses=@($name)} $name
 $f=Fixture;Reject {Invoke $f -Apply} ('Running application refuses publication: '+$name)
}
& $guard {$script:FixtureProcesses=@('fixture_editor_closed')}
foreach($target in @('Project-Library-Client/Runtime/python.exe','magix_projects.py','MAGIX-All-Projects-Index.json','INTERLINKED-App/web/style.css','INTERLINKED-Desktop/Violet.png','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe')){
 $f=Fixture;Put (Join-Path $f.Context.Kit $target) 'tampered baseline'
 Reject {Invoke $f -Apply} ('Changed installed baseline refuses publication: '+$target)
}
$f=Fixture;Reject {Invoke-CoolerPublication -Context $f.Context -ManifestPath $f.Plan -ManifestSha256 ('0'*64) -ReceiptDirectory $f.Receipts -Apply} 'Wrong reviewed plan hash is refused'
$f=Fixture;Put (Join-Path $f.Source $worker) 'source tampered';Reject {Invoke $f -Apply} 'Changed reviewed source is refused'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-App/web/style.css') 'unrelated changed GUI';Refresh-Plan $f;Reject {Invoke $f -Apply} 'Reviewed plan cannot add a changed GUI row'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-Desktop/Violet.png') 'replacement art';Refresh-Plan $f;Reject {Invoke $f -Apply} 'Reviewed plan cannot replace approved Violet artwork'
$f=Fixture;$seal=Get-Content (Join-Path $f.Source $sealKey) -Raw|ConvertFrom-Json;$seal.files.'INTERLINKED.Master.Worker.exe'='0'*64;Json (Join-Path $f.Source $sealKey) $seal;Refresh-Plan $f;Reject {Invoke $f -Apply} 'Prospective seal must match the reviewed worker'
foreach($metadata in @('sdkVersion','sdkArchiveSha256','createdUtc')){
 $f=Fixture;$seal=Get-Content (Join-Path $f.Source $sealKey) -Raw|ConvertFrom-Json
 $seal.$metadata='invalid fixture metadata';Json (Join-Path $f.Source $sealKey) $seal;Refresh-Plan $f
 Reject {Invoke $f -Apply} ('Prospective desktop seal rejects invalid provenance: '+$metadata)
}
$f=Fixture;$seal=Get-Content (Join-Path $f.Context.Kit $sealKey) -Raw|ConvertFrom-Json;$seal.files.'Violet.png'='0'*64;Json (Join-Path $f.Context.Kit $sealKey) $seal;$row=$f.Rows|Where-Object path -ceq $sealKey;$row.beforeSha256=Digest (Join-Path $f.Context.Kit $sealKey);Refresh-Plan $f;Reject {Invoke $f -Apply} 'Installed complete desktop seal must match every old payload'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files[0].path='../outside';Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Path traversal is refused'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$outside=Join-Path $output 'outside-plan-source';[IO.File]::Copy($plan.files[0].source,$outside);$plan.files[0].source=$outside;Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Hash-valid source outside its workspace is refused'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files|Select-Object -Skip 1);Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Incomplete 29-row plan is refused'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files+=$plan.files[0];Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Duplicate plan row is refused'
foreach($which in @('source','target','plan')){
 $f=Fixture;$path=if($which-ceq 'source'){Join-Path $f.Source $worker}elseif($which-ceq 'target'){Join-Path $f.Context.Kit $worker}else{$f.Plan}
 New-Item -ItemType HardLink -Path ($path+'.hardlink') -Target $path|Out-Null
 Reject {Invoke $f -Apply} ('Hardlinked '+$which+' is refused')
}
$f=Fixture;$ordinary=Join-Path $f.Context.Workspace 'ordinary-source';[void][IO.Directory]::CreateDirectory($ordinary)
$junction=Join-Path $f.Context.Workspace 'junction-source';New-Item -ItemType Junction -Path $junction -Target $ordinary|Out-Null
Put (Join-Path $ordinary 'worker.exe') 'new synthetic worker; never executed';$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json
($plan.files|Where-Object path -ceq $worker).source=Join-Path $junction 'worker.exe';Json $f.Plan $plan
Reject {Invoke $f -Apply} 'Junction source ancestor is refused'
$report=[pscustomobject]@{ok=$true;checks=@($checks);count=$checks.Count;productionTouched=$false;usbRequested=$false}
Json (Join-Path $output 'cooler-publication-tests.json') $report
$report|ConvertTo-Json -Depth 8
