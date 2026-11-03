# Synthetic publication fixtures only. Never reads or writes the installed kit.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$module=Import-Module (Join-Path $PSScriptRoot 'Interlinked-Desktop-Publication.psm1') -Force -PassThru
# Process enumeration is independent of the file/lock engine. Fixture processes
# are injected in this test's module scope, never via the production publisher.
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='fixture_editor_closed'})}}
$root=Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) ('dp-'+[guid]::NewGuid().ToString('N').Substring(0,8))
[void][IO.Directory]::CreateDirectory($root)
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
 'INTERLINKED-App/interlinked_stats.py','INTERLINKED-App/interlinked_stats_process.py',
 'INTERLINKED-App/interlinked_configuration.py',
 'INTERLINKED-App/interlinked_ai.py','INTERLINKED-App/interlinked_ai_node.py','INTERLINKED-App/interlinked_ai_workspaces.py',
 'INTERLINKED-App/interlinked_ai_bridge.py','INTERLINKED-App/interlinked_ai_ipc.py','INTERLINKED-App/ai_bridge.py',
 'INTERLINKED-App/interlinked_history_import.py','INTERLINKED-App/interlinked_history_store.py','INTERLINKED-App/interlinked_history_service.py',
 'INTERLINKED-App/interlinked_history_runtime.py','INTERLINKED-App/interlinked_history_gateway.py',
 'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
 'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config','INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll',
 'INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe.config','INTERLINKED-Desktop/Violet.png',
 'INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll','INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt',
 'INTERLINKED-Desktop/WebView2-NOTICE.txt','INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
function Fixture {
 param([switch]$V2Baseline,[switch]$V3Baseline,[switch]$V4Baseline,[switch]$V5Baseline,[switch]$V6Baseline,[switch]$V7Baseline,[switch]$V8Baseline,[switch]$V9Baseline,[switch]$DesktopInstalled)
 $workspace=Join-Path $root ([guid]::NewGuid().ToString('N').Substring(0,8));$kit=Join-Path $workspace 'kit';$source=Join-Path $workspace 'source';$receipts=Join-Path $workspace 'receipts'
 foreach($dir in @($kit,$source,$receipts)){[void][IO.Directory]::CreateDirectory($dir)}
 Put (Join-Path $kit 'INTERLINKED.cmd') '@echo OLD'
 if($DesktopInstalled -or $V2Baseline -or $V3Baseline -or $V4Baseline -or $V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  Put (Join-Path $kit 'INTERLINKED.cmd') '@echo DESKTOP PREVIOUS'
  Put (Join-Path $kit 'INTERLINKED-Browser.cmd') '@echo OLD'
 }
 Put (Join-Path $kit 'INTERLINKED-App/interlinked_gui.py') 'old backend'
 $oldFiles=[ordered]@{}
 foreach($name in @('interlinked_gui.py','web/index.html','web/app.js','web/style.css')){
  $path=Join-Path $kit ('INTERLINKED-App/'+$name);if(-not(Test-Path -LiteralPath $path)){Put $path ('fixture '+$name)};$oldFiles[$name]=Digest $path
 }
 if($V2Baseline -or $V3Baseline -or $V4Baseline -or $V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  foreach($name in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py')){
   $path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  }
 }
 if($V3Baseline -or $V4Baseline -or $V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  foreach($name in @('interlinked_master.py','master_hook.py','master_bridge.py')){
   $path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  }
 }
 if($V4Baseline -or $V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  $name='interlinked_jobs.py';$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
 }
 if($V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  $name='interlinked_buddy.py';$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
 }
 Put (Join-Path $kit 'MAGIX-Media-Audit.json') '{"fixture":true}'
 $oldSeal=[ordered]@{format='interlinked-gui-checksums-v1';files=$oldFiles;auditSha256=Digest (Join-Path $kit 'MAGIX-Media-Audit.json')}
 if($V2Baseline){$oldSeal.format='interlinked-gui-checksums-v2'}
 if($V3Baseline){$oldSeal.format='interlinked-gui-checksums-v3'}
 if($V4Baseline){$oldSeal.format='interlinked-gui-checksums-v4'}
 if($V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){$oldSeal.format='interlinked-gui-checksums-v5'}
 if($V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  foreach($name in @('interlinked_stats.py','interlinked_stats_process.py')){
   $path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  }
  $oldSeal.format='interlinked-gui-checksums-v6'
 }
 if($V7Baseline -or $V8Baseline -or $V9Baseline){
  $name='interlinked_configuration.py';$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  $oldSeal.format='interlinked-gui-checksums-v7'
 }
 if($V8Baseline -or $V9Baseline){
  foreach($name in @('interlinked_ai.py','interlinked_ai_node.py','interlinked_ai_workspaces.py')){$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path}
  $oldSeal.format='interlinked-gui-checksums-v8'
 }
 if($V9Baseline){
  foreach($name in @('interlinked_ai_bridge.py','interlinked_ai_ipc.py','ai_bridge.py')){$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path}
  $oldSeal.format='interlinked-gui-checksums-v9'
 }
 Json (Join-Path $kit 'INTERLINKED-App/GUI-CHECKSUMS.json') $oldSeal
 foreach($name in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){Put (Join-Path $kit $name) ''}
 foreach($name in @('binding.json','multi-binding.json')){Json (Join-Path $kit ('MAGIX-Workspace-State/'+$name)) @{status='UNBOUND'}}
 $pins=@{};foreach($name in @('magix_projects.py','magix_workspace.py','MAGIX-All-Projects-Index.json')){Put (Join-Path $kit $name) ('pinned '+$name);$pins[$name]=Digest (Join-Path $kit $name)}
 $client=Join-Path $kit 'Project-Library-Client';$sealed=@()
 foreach($name in @('project_library.py','Runtime/python.exe')){Put (Join-Path $client $name) ('sealed fixture '+$name);$path=Join-Path $client $name;$sealed+=@{path=$name;bytes=(Get-Item -LiteralPath $path).Length;sha256=Digest $path}}
 Json (Join-Path $client 'HELPER-CHECKSUMS.json') @{format='project-library-client-checksums-v1';files=$sealed}
 $context=[pscustomobject]@{Workspace=$workspace;Kit=$kit;Allowed=$allowed;Pins=$pins;ClientFileCount=2;ClientSealSha256=Digest (Join-Path $client 'HELPER-CHECKSUMS.json')}
 foreach($name in $allowed){Put (Join-Path $source $name) ('new fixture '+$name)}
 Put (Join-Path $source 'INTERLINKED-Browser.cmd') '@echo OLD'
 # Ordinary updates retain the launcher's Buddy dependency. Updating Buddy's
 # bytes requires the separately reviewed pin-only migration fixture below.
 $buddyInstalled=Join-Path $kit 'INTERLINKED-App/interlinked_buddy.py'
 $buddySource=Join-Path $source 'INTERLINKED-App/interlinked_buddy.py'
 if(Test-Path -LiteralPath $buddyInstalled){Put $buddySource ([IO.File]::ReadAllText($buddyInstalled))}
 Put (Join-Path $kit 'magix_projects.py') ("# Synthetic reviewed launcher`r`nBUDDY_SHA='"+(Digest $buddySource)+"'`r`n")
 $pins['magix_projects.py']=Digest (Join-Path $kit 'magix_projects.py')
 Put (Join-Path $source 'INTERLINKED-App/interlinked_gui.py') ("# Synthetic reviewed GUI`r`nMAGIX_SHA='"+$pins['magix_projects.py']+"'`r`n")
 $newFiles=[ordered]@{}
 foreach($name in @('interlinked_gui.py','interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','interlinked_buddy.py','interlinked_stats.py','interlinked_stats_process.py','interlinked_configuration.py','interlinked_ai.py','interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_ai_bridge.py','interlinked_ai_ipc.py','ai_bridge.py','interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py','web/index.html','web/app.js','web/style.css')){
  $newFiles[$name]=Digest (Join-Path $source ('INTERLINKED-App/'+$name))
 }
 Json (Join-Path $source 'INTERLINKED-App/GUI-CHECKSUMS.json') @{format='interlinked-gui-checksums-v10';files=$newFiles;auditSha256=$oldSeal.auditSha256}
 $desktopFiles=[ordered]@{};foreach($name in $allowed | Where-Object {$_ -like 'INTERLINKED-Desktop/*' -and $_ -notlike '*/DESKTOP-CHECKSUMS.json'}){$desktopFiles[$name.Substring(20)]=Digest (Join-Path $source $name)}
 Json (Join-Path $source 'INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json') @{format='interlinked-desktop-checksums-v1';files=$desktopFiles}
 if($DesktopInstalled -or $V2Baseline -or $V3Baseline -or $V4Baseline -or $V5Baseline -or $V6Baseline -or $V7Baseline -or $V8Baseline -or $V9Baseline){
  foreach($name in $allowed | Where-Object {$_ -like 'INTERLINKED-Desktop/*'}){Put (Join-Path $kit $name) ('previous desktop '+$name)}
 }
 $rows=@();foreach($name in $allowed){$path=Join-Path $kit $name;$rows+=@{path=$name;source=Join-Path $source $name;sha256=Digest (Join-Path $source $name);beforeSha256=$(if(Test-Path -LiteralPath $path){Digest $path}else{$null})}}
 $plan=Join-Path $workspace 'plan.json';Json $plan @{format='interlinked-desktop-publication-v1';files=$rows}
 [pscustomobject]@{Context=$context;Plan=$plan;Rows=$rows;Source=$source;Receipts=$receipts}
}
function Refresh-Plan($F){
 foreach($row in $F.Rows){$row.sha256=Digest $row.source;$target=Join-Path $F.Context.Kit $row.path;$row.beforeSha256=if(Test-Path -LiteralPath $target){Digest $target}else{$null}}
 Json $F.Plan @{format='interlinked-desktop-publication-v1';files=$F.Rows}
}
function Invoke($F,[switch]$Apply){Invoke-DesktopPublication -Context $F.Context -ManifestPath $F.Plan -ManifestSha256 (Digest $F.Plan) -ReceiptDirectory $F.Receipts -Apply:$Apply}

function Refresh-GuiSeal($F){
 $path=Join-Path $F.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
 foreach($name in @($seal.files.PSObject.Properties.Name)){$seal.files.$name=Digest (Join-Path $F.Source ('INTERLINKED-App/'+$name))}
 Json $path $seal
}
function Refresh-MigrationCandidate($F){
 $F.Context.LauncherMigration.afterSha256=Digest (Join-Path $F.Source 'magix_projects.py')
 Put (Join-Path $F.Source 'INTERLINKED-App/interlinked_gui.py') ("# Synthetic GUI source`r`nMAGIX_SHA='"+$F.Context.LauncherMigration.afterSha256+"'`r`n")
 Refresh-GuiSeal $F;Refresh-Plan $F
}
function MigrationFixture {
 $f=Fixture -V5Baseline
 Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_buddy.py') 'new reviewed migration Buddy bytes'
 $beforeBuddy=Digest (Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_buddy.py')
 $afterBuddy=Digest (Join-Path $f.Source 'INTERLINKED-App/interlinked_buddy.py')
 # Genuine Python assignment text and CRLFs; the fixture contains no production
 # payload. Only these 64 ASCII digest bytes may change in the launcher.
 $beforeText="# Synthetic MAGIX launcher`r`nfrom pathlib import Path`r`nBUDDY_SHA='"+$beforeBuddy+"'`r`ndef buddy_path(root):`r`n    return Path(root) / 'INTERLINKED-App' / 'interlinked_buddy.py'`r`n"
 Put (Join-Path $f.Context.Kit 'magix_projects.py') $beforeText
 Put (Join-Path $f.Source 'magix_projects.py') $beforeText.Replace($beforeBuddy,$afterBuddy)
 $before=Digest (Join-Path $f.Context.Kit 'magix_projects.py')
 $f.Context.Pins['magix_projects.py']=$before;$f.Context.Allowed+=@('magix_projects.py')
 $f.Context | Add-Member -NotePropertyName LauncherMigration -NotePropertyValue ([pscustomobject]@{path='magix_projects.py';beforeSha256=$before;afterSha256=Digest (Join-Path $f.Source 'magix_projects.py');beforeBuddySha256=$beforeBuddy;afterBuddySha256=$afterBuddy})
 $f.Rows+=@{path='magix_projects.py';source=Join-Path $f.Source 'magix_projects.py';sha256=$f.Context.LauncherMigration.afterSha256;beforeSha256=$before}
 Refresh-MigrationCandidate $f
 return $f
}

foreach($status in @('STARTING','RUNNING','STOPPING','RECOVERY_REQUIRED')){
 $f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/stats/session.json') @{format='interlinked-stats-session-v1';status=$status}
 Reject {Invoke $f -Apply} ('Unfinished Stats journal blocks publication: '+$status)
}
foreach($companion in @('interlinked_stats.py','interlinked_stats_process.py')){
 $f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json;$seal.files.PSObject.Properties.Remove($companion);Json $sealPath $seal;Refresh-Plan $f
 Reject {Invoke $f -Apply} ('Missing prospective Stats companion is refused: '+$companion)
}

foreach($baseline in @('v6','v7')){
 $f=if($baseline -eq 'v6'){Fixture -V6Baseline}else{Fixture -V7Baseline}
 $result=Invoke $f -Apply
 Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) ('Existing '+$baseline+' configuration-compatible baseline upgrades with exact hashes')
}

$f=Fixture;$before=Digest (Join-Path $f.Context.Kit 'INTERLINKED.cmd');$result=Invoke $f
Check ($result.status -ceq 'PREFLIGHT_PASSED') 'Dry-run preflight passes isolated valid package'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED.cmd')) -ceq $before -and -not(Test-Path (Join-Path $f.Context.Kit 'INTERLINKED-Desktop'))) 'Dry-run writes no installed app bytes'
foreach($lock in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){
 $f=Fixture;$handle=[IO.File]::Open((Join-Path $f.Context.Kit $lock),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
 try{Reject {Invoke $f -Apply} ('Held session lock prevents publication: '+$lock)}finally{$handle.Dispose()}
 Check (-not(Test-Path (Join-Path $f.Context.Kit 'INTERLINKED-Desktop'))) 'Held lock leaves package absent'
}
foreach($journal in @('binding.json','multi-binding.json')){$f=Fixture;Json (Join-Path $f.Context.Kit ('MAGIX-Workspace-State/'+$journal)) @{status='BOUND'};Reject {Invoke $f -Apply} ('Bound journal blocks publication: '+$journal)}
$f=Fixture;Put (Join-Path $f.Context.Kit 'Project-Library-Client/Runtime/python.exe') 'tampered';Reject {Invoke $f -Apply} 'Changed sealed runtime blocks publication'
$f=Fixture;Put (Join-Path $f.Context.Kit 'magix_projects.py') 'tampered';Reject {Invoke $f -Apply} 'Changed accepted MAGIX launcher blocks publication'
$f=Fixture;Put (Join-Path $f.Context.Kit 'INTERLINKED-App/web/style.css') 'tampered';Reject {Invoke $f -Apply} 'Changed existing static UI seal blocks publication'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED.cmd') 'tampered';Reject {Invoke $f -Apply} 'Changed reviewed source blocks publication'
$f=Fixture;Put (Join-Path $f.Context.Kit 'INTERLINKED.cmd') 'tampered';Reject {Invoke $f -Apply} 'Changed target before hash blocks publication'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files[0].path='../outside.cmd';Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Path traversal target is refused'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files+=$plan.files[0];Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Duplicate publication targets are refused'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files | Select-Object -Skip 1);Json $f.Plan $plan;Reject {Invoke $f -Apply} 'Incomplete publication plan is refused'
$f=Fixture;$path=Join-Path $f.Context.Kit 'INTERLINKED.cmd';New-Item -ItemType HardLink -Path (Join-Path $f.Context.Kit 'other-hardlink.cmd') -Target $path|Out-Null;Reject {Invoke $f -Apply} 'Hardlinked existing publication target is refused'
$f=Fixture;Reject {Invoke-DesktopPublication -Context $f.Context -ManifestPath $f.Plan -ManifestSha256 ('0'*64) -ReceiptDirectory $f.Receipts -Apply} 'Wrong reviewed plan hash is refused'
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='videodeluxe'})}}
$f=Fixture;Reject {Invoke $f -Apply} 'Running MAGIX process blocks publication'
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='fixture_editor_closed'})}}
$f=Fixture;$before=Digest (Join-Path $f.Context.Kit 'INTERLINKED.cmd');$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Complete MASTER, MSI Jobs and Buddy package publishes and readback passes'
$receipt=Get-Content $result.receipt -Raw|ConvertFrom-Json
Check ($receipt.files[-1].path -ceq 'INTERLINKED.cmd') 'New desktop launcher publishes last'
$wrapperRow=$receipt.files|Where-Object path -ceq 'INTERLINKED.cmd'
Check ((Digest $wrapperRow.backup) -ceq $before) 'Prior installed launcher is preserved byte exactly'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd')) -ceq $before) 'Browser recovery launcher preserves previous workflow'
Check (-not $receipt.projectFilesWritten -and -not $receipt.workspaceStateWritten) 'Receipt distinguishes app publication from workspace changes'
Check ((Get-Content (Join-Path $f.Context.Kit 'MAGIX-Workspace-State/multi-binding.json') -Raw|ConvertFrom-Json).status -ceq 'UNBOUND') 'Publication retains workspace binding'

$f=Fixture -DesktopInstalled;$browserBefore=Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd');$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED') 'Installed desktop v1 GUI upgrades to the v7 recovery-compatible seal'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd')) -ceq $browserBefore) 'Desktop upgrade preserves existing browser recovery bytes rather than desktop wrapper'
$f=Fixture -V2Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v2 seven-file GUI upgrades with all companion hashes checked'
$installed=Get-Content (Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json') -Raw|ConvertFrom-Json
Check ($installed.format -ceq 'interlinked-gui-checksums-v10' -and @($installed.files.PSObject.Properties).Count -eq 26) 'Installed MASTER, MSI Jobs and Buddy seal covers exactly twenty-six app and web files'
$f=Fixture -V3Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v3 MASTER baseline upgrades with exact companion hashes checked'
$f=Fixture -V4Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v4 MSI Jobs baseline upgrades with exact companion hashes checked'
$f=Fixture -V5Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v5 Buddy baseline updates with exact companion hashes checked'

$f=Fixture -V8Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v8 AI baseline upgrades to exact v10 history seal'

$f=Fixture -V9Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 40) 'Existing v9 bridge baseline upgrades to exact v10 history seal'
foreach($companion in @('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py')){
 $f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json;$seal.files.PSObject.Properties.Remove($companion);Json $sealPath $seal;Refresh-Plan $f
 Reject {Invoke $f -Apply} ('Missing history seal member is refused: '+$companion)
}
$f=Fixture -V9Baseline;Put (Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_ai_bridge.py') 'changed bridge'
Reject {Invoke $f -Apply} 'Changed historical v9 bridge baseline is refused'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v9';foreach($name in @($seal.files.PSObject.Properties.Name | Where-Object {$_ -like 'interlinked_history_*'})){$seal.files.PSObject.Properties.Remove($name)};Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective exact legacy v9 seal cannot publish the history release'

foreach($companion in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','interlinked_buddy.py')){
 $f=Fixture;Put (Join-Path $f.Source ('INTERLINKED-App/'+$companion)) 'tampered companion'
 Reject {Invoke $f -Apply} ('Changed reviewed companion source refused: '+$companion)
 $f=Fixture -V5Baseline;Put (Join-Path $f.Context.Kit ('INTERLINKED-App/'+$companion)) 'tampered installed companion'
 Reject {Invoke $f -Apply} ('Changed installed companion seal refused: '+$companion)
}
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.files.PSObject.Properties.Remove('interlinked_mw5.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective seal missing a companion is refused'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v1';foreach($name in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','interlinked_buddy.py')){$seal.files.PSObject.Properties.Remove($name)};Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective legacy four-file GUI seal cannot publish MASTER'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v2';foreach($name in @('interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','interlinked_buddy.py')){$seal.files.PSObject.Properties.Remove($name)};Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective seven-file v2 seal cannot omit MASTER modules'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v3';$seal.files.PSObject.Properties.Remove('interlinked_jobs.py');$seal.files.PSObject.Properties.Remove('interlinked_buddy.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective ten-file v3 seal cannot omit MSI Jobs'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.files.PSObject.Properties.Remove('interlinked_jobs.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective v5 seal missing MSI Jobs is refused'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_jobs.py') 'different reviewed MSI Jobs source';Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective MSI Jobs seal mismatch is refused even with a reviewed plan hash'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files | Where-Object path -cne 'INTERLINKED-App/interlinked_jobs.py');Json $f.Plan $plan
Reject {Invoke $f -Apply} 'Publication plan missing MSI Jobs is refused'
$f=Fixture -V4Baseline;[IO.File]::Delete((Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_jobs.py'))
Reject {Invoke $f -Apply} 'Missing installed v4 MSI Jobs module is refused'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v4';$seal.files.PSObject.Properties.Remove('interlinked_buddy.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective exact eleven-file v4 seal cannot downgrade Buddy'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.files.PSObject.Properties.Remove('interlinked_buddy.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective v5 seal missing Buddy is refused'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_buddy.py') 'different reviewed Buddy source';Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective Buddy seal mismatch is refused even with a reviewed plan hash'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files | Where-Object path -cne 'INTERLINKED-App/interlinked_buddy.py');Json $f.Plan $plan
Reject {Invoke $f -Apply} 'Publication plan missing Buddy is refused'
$f=Fixture -V5Baseline;[IO.File]::Delete((Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_buddy.py'))
Reject {Invoke $f -Apply} 'Missing installed v5 Buddy module is refused'
foreach($name in @('interlinked_master.py','master_hook.py','master_bridge.py')){
 $f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
 $seal.files.PSObject.Properties.Remove($name);Json $sealPath $seal;Refresh-Plan $f
 Reject {Invoke $f -Apply} ('Prospective MASTER seal requires each new module: '+$name)
 $f=Fixture -V3Baseline;[IO.File]::Delete((Join-Path $f.Context.Kit ('INTERLINKED-App/'+$name)))
 Reject {Invoke $f -Apply} ('Missing installed v3 module is refused: '+$name)
}
foreach($name in @('INTERLINKED.Master.Worker.exe','INTERLINKED.Master.Worker.exe.config','Violet.png')){
 $f=Fixture;Put (Join-Path $f.Source ('INTERLINKED-Desktop/'+$name)) 'different reviewed MASTER payload';Refresh-Plan $f
 Reject {Invoke $f -Apply} ('Desktop package seal must cover exact MASTER payload: '+$name)
}
$f=Fixture -V2Baseline;$sealPath=Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='INTERLINKED-GUI-CHECKSUMS-V2';Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Baseline GUI seal formats remain case exact'
$f=Fixture -DesktopInstalled;Put (Join-Path $f.Source 'INTERLINKED-Browser.cmd') '@echo changed recovery';Refresh-Plan $f
Reject {Invoke $f -Apply} 'A reviewed plan cannot replace the existing browser recovery wrapper'
$f=Fixture;$path=Join-Path $f.Source 'INTERLINKED-App/interlinked_mw5.py';New-Item -ItemType HardLink -Path (Join-Path $f.Source 'hardlinked-companion.py') -Target $path|Out-Null
Reject {Invoke $f -Apply} 'Hardlinked companion source is refused'
foreach($state in @('STAGED','EDITING','COLLECTED','APPLYING','REVIEW_REQUIRED')){
 $f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5/session.json') @{format='interlinked-mw5-session-v1';status=$state}
 Reject {Invoke $f -Apply} ('Outstanding MW5 session blocks publication: '+$state)
}
$f=Fixture;[void][IO.Directory]::CreateDirectory((Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5'))
Reject {Invoke $f -Apply} 'Incomplete MW5 state directory blocks publication'
$f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5/session.json') @{format='unreviewed-session';status='IDLE'}
Reject {Invoke $f -Apply} 'Unknown MW5 journal format cannot assert an idle session'
$f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5/session.json') @{format='interlinked-mw5-session-v1';status='IDLE'}
$journal=Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5/session.json';New-Item -ItemType HardLink -Path (Join-Path $f.Context.Kit 'other-mw5-session.json') -Target $journal|Out-Null
Reject {Invoke $f -Apply} 'Hardlinked MW5 session journal is refused'
$f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/folder-session.json') @{format='interlinked-folder-session-v1';status='ACTIVE'}
Reject {Invoke $f -Apply} 'Active managed folder session blocks publication'
$f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/folder-session.json') @{format='interlinked-folder-session-v1';status='IDLE'}
$journal=Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/folder-session.json';New-Item -ItemType HardLink -Path (Join-Path $f.Context.Kit 'other-folder-session.json') -Target $journal|Out-Null
Reject {Invoke $f -Apply} 'Hardlinked folder session journal is refused'
$f=Fixture -V2Baseline;[IO.File]::Delete((Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_hub.py'))
Reject {Invoke $f -Apply} 'Missing installed v2 companion is refused'
$f=Fixture;Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/mw5/session.json') @{format='interlinked-mw5-session-v1';status='IDLE'}
Json (Join-Path $f.Context.Kit 'INTERLINKED-GUI-State/folder-session.json') @{format='interlinked-folder-session-v1';status='IDLE'}
Check ((Invoke $f).status -ceq 'PREFLIGHT_PASSED') 'Completed MW5 and folder journals permit preflight'
foreach($name in @('UE4Editor','UnrealEditor','MechWarrior5Editor-Win64-Shipping')){
 & $module {param($ProcessName) $script:FixtureProcessName=$ProcessName;function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName=$script:FixtureProcessName})}} $name
 $f=Fixture;Reject {Invoke $f -Apply} ('Running MW5 editor process blocks publication: '+$name)
}
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='INTERLINKED.Master.Worker'})}}
$f=Fixture;Reject {Invoke $f -Apply} 'Running MASTER worker blocks publication'
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='fixture_editor_closed'})}}
# The optional Buddy launcher migration extends only the exact reviewed target.
$f=Fixture -V5Baseline;$before=Digest (Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_buddy.py')
Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_buddy.py') 'resealed but incompatible Buddy';Refresh-GuiSeal $f;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Ordinary full publication rejects resealed Buddy bytes outside the retained launcher pin'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_buddy.py')) -ceq $before) 'Incompatible ordinary Buddy update writes no companion bytes'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_gui.py') ("MAGIX_SHA='"+('f'*64)+"'`r`n");Refresh-GuiSeal $f;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Ordinary full publication rejects resealed GUI with a wrong retained launcher pin'
$f=MigrationFixture;$before=$f.Context.LauncherMigration.beforeSha256;$result=Invoke $f
Check ($result.status -ceq 'PREFLIGHT_PASSED' -and $result.files -eq 41) 'Pin-only launcher migration dry-run accepts exactly forty-one members'
Check ((Digest (Join-Path $f.Context.Kit 'magix_projects.py')) -ceq $before) 'Migration dry-run preserves installed launcher bytes'
$result=Invoke $f -Apply;$receipt=Get-Content -LiteralPath $result.receipt -Raw|ConvertFrom-Json
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 41) 'Pin-only launcher migration publishes the complete v10 package'
Check (-not $receipt.acceptedLauncherUnchanged -and $receipt.launcherMigration.afterSha256 -ceq $f.Context.LauncherMigration.afterSha256) 'Migration receipt reports the exact reviewed before/after transition'
$row=$receipt.files|Where-Object path -ceq 'magix_projects.py'
Check ((Digest $row.backup) -ceq $before -and (Digest (Join-Path $f.Context.Kit 'magix_projects.py')) -ceq $row.sha256) 'Migration preserves original launcher provenance and verifies replacement bytes'
Check ((Digest (Join-Path $f.Context.Kit 'Project-Library-Client/HELPER-CHECKSUMS.json')) -ceq $f.Context.ClientSealSha256) 'Migration preserves original library seal'
foreach($name in @('magix_workspace.py','MAGIX-All-Projects-Index.json')){Check ((Digest (Join-Path $f.Context.Kit $name)) -ceq $f.Context.Pins[$name]) ('Migration preserves other accepted dependency: '+$name)}
Check ($receipt.files[-1].path -ceq 'INTERLINKED.cmd' -and -not $receipt.projectFilesWritten -and -not $receipt.workspaceStateWritten) 'Migration retains wrapper ordering and workspace separation'
Reject {Invoke $f -Apply} 'Completed migration cannot replay against the old accepted baseline'
& $module {param($Context) Assert-DesktopBaseline $Context -AfterMigration} $f.Context
Check $true 'Post-migration baseline uses only the reviewed new launcher pin'
Put (Join-Path $f.Context.Kit 'magix_projects.py') '# changed after publication'
Reject {& $module {param($Context) Assert-DesktopBaseline $Context -AfterMigration} $f.Context} 'Post-migration baseline refuses changed launcher bytes'

foreach($field in @('beforeSha256','afterSha256','beforeBuddySha256','afterBuddySha256')){
 $f=MigrationFixture;$f.Context.LauncherMigration.$field='0'*64
 Reject {Invoke $f -Apply} ('Migration refuses mismatched reviewed hash: '+$field)
 Check (@(Get-ChildItem -LiteralPath $f.Context.Kit -Filter 'magix_projects.py.before-desktop-*').Count -eq 0) 'Rejected migration creates no launcher backup'
}
$f=MigrationFixture;$f.Context.LauncherMigration.path='magix_workspace.py';Reject {Invoke $f -Apply} 'Migration refuses any target except magix_projects.py'
$f=MigrationFixture;$f.Context.LauncherMigration|Add-Member -NotePropertyName extraTarget -NotePropertyValue 'magix_workspace.py';Reject {Invoke $f -Apply} 'Migration refuses extra metadata authority'
$f=MigrationFixture;$f.Context.PSObject.Properties.Remove('LauncherMigration');Reject {Invoke $f -Apply} 'Launcher plan row without reviewed migration is refused'
$f=MigrationFixture;$f.Rows=@($f.Rows|Where-Object path -cne 'magix_projects.py');$f.Context.Allowed=@($f.Context.Allowed|Where-Object {$_ -cne 'magix_projects.py'});Refresh-Plan $f
Reject {Invoke $f -Apply} 'Reviewed migration requires its launcher plan row'
$f=MigrationFixture;$f.Context.Allowed+=@('magix_workspace.py');$source=Join-Path $f.Source 'magix_workspace.py';Put $source 'replacement forbidden';$f.Rows+=@{path='magix_workspace.py';source=$source;sha256=Digest $source;beforeSha256=$f.Context.Pins['magix_workspace.py']};Refresh-Plan $f
Reject {Invoke $f -Apply} 'Migration cannot widen replacement authority to another pinned dependency'
$f=MigrationFixture;$path=Join-Path $f.Source 'magix_projects.py';Put $path ((Get-Content -LiteralPath $path -Raw)+"# unauthorized edit`r`n");Refresh-MigrationCandidate $f
Reject {Invoke $f -Apply} 'Even resealed launcher changes beyond BUDDY_SHA are refused'
$f=MigrationFixture;$path=Join-Path $f.Source 'magix_projects.py';Put $path ((Get-Content -LiteralPath $path -Raw).Replace("`r`n","`n"));Refresh-MigrationCandidate $f
Reject {Invoke $f -Apply} 'Launcher newline normalization is not a pin-only migration'
$f=MigrationFixture;$path=Join-Path $f.Context.Kit 'magix_projects.py';Put $path ((Get-Content -LiteralPath $path -Raw)+"BUDDY_SHA='"+$f.Context.LauncherMigration.beforeBuddySha256+"'`r`n");$f.Context.LauncherMigration.beforeSha256=Digest $path;$f.Context.Pins['magix_projects.py']=Digest $path;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Ambiguous repeated Buddy assignment is refused'
$f=MigrationFixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_buddy.py') 'another reviewed-looking Buddy';Refresh-GuiSeal $f;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Resealed Buddy bytes must match the launcher pin and migration hash'
$f=MigrationFixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_gui.py') ("MAGIX_SHA='"+('f'*64)+"'`r`n");Refresh-GuiSeal $f;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Resealed GUI must pin the exact migrated launcher'
$f=MigrationFixture;$path=Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_buddy.py';Put $path 'wrong installed Buddy';$sealPath=Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content -LiteralPath $sealPath -Raw|ConvertFrom-Json;$seal.files.'interlinked_buddy.py'=Digest $path;Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Installed Buddy baseline must match the accepted launcher pin'
$f=MigrationFixture;$path=Join-Path $f.Source 'magix_projects.py';New-Item -ItemType HardLink -Path (Join-Path $f.Source 'other-launcher.py') -Target $path|Out-Null
Reject {Invoke $f -Apply} 'Hardlinked migration source is refused'
foreach($lock in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){
 $f=MigrationFixture;$handle=[IO.File]::Open((Join-Path $f.Context.Kit $lock),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
 try{Reject {Invoke $f -Apply} ('Migration respects active session lock: '+$lock)}finally{$handle.Dispose()}
 Check ((Digest (Join-Path $f.Context.Kit 'magix_projects.py')) -ceq $f.Context.LauncherMigration.beforeSha256) 'Held migration lock preserves launcher baseline'
}
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='INTERLINKED'})}}
$f=MigrationFixture;Reject {Invoke $f -Apply} 'Migration refuses an active desktop process'
& $module {function script:Get-Process {param($ErrorAction) @([pscustomobject]@{ProcessName='fixture_editor_closed'})}}
$f=MigrationFixture;Json (Join-Path $f.Context.Kit 'MAGIX-Workspace-State/multi-binding.json') @{status='BOUND'}
Reject {Invoke $f -Apply} 'Migration refuses an active MAGIX journal'
# Permit reads of the final wrapper, but deny replacement. Earlier files can be
# published; failure must preserve their backups and a truthful partial receipt.
$f=MigrationFixture;$handle=[IO.File]::Open((Join-Path $f.Context.Kit 'INTERLINKED.cmd'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{Reject {Invoke $f -Apply} 'Late replacement failure stops migration without concealing partial publication'}finally{$handle.Dispose()}
$receipts=@(Get-ChildItem -LiteralPath $f.Receipts -Filter 'desktop-publication-*.json');$receipt=Get-Content -LiteralPath $receipts[0].FullName -Raw|ConvertFrom-Json
$row=$receipt.files|Where-Object path -ceq 'magix_projects.py'
Check ($receipt.status -ceq 'PUBLICATION_STOPPED' -and $null -ne $row -and (Digest $row.backup) -ceq $f.Context.LauncherMigration.beforeSha256) 'Interrupted migration retains exact original launcher and partial result evidence'
Reject {Invoke $f -Apply} 'Interrupted migration cannot silently replay the old baseline'
Json (Join-Path $root 'publication-test-results.json') @{status='PASS';checks=@($checks);count=$checks.Count;scope='Synthetic files, locks, seals and publication only; no production update or MAGIX launched.'}
[pscustomobject]@{status='PASS';checks=$checks.Count;root=$root}|ConvertTo-Json
