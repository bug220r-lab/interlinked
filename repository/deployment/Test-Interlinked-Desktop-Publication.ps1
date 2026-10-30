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
 'INTERLINKED-App/interlinked_jobs.py',
 'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
 'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config','INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll',
 'INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe.config','INTERLINKED-Desktop/Violet.png',
 'INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll','INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt',
 'INTERLINKED-Desktop/WebView2-NOTICE.txt','INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
function Fixture {
 param([switch]$V2Baseline,[switch]$V3Baseline,[switch]$V4Baseline,[switch]$DesktopInstalled)
 $workspace=Join-Path $root ([guid]::NewGuid().ToString('N').Substring(0,8));$kit=Join-Path $workspace 'kit';$source=Join-Path $workspace 'source';$receipts=Join-Path $workspace 'receipts'
 foreach($dir in @($kit,$source,$receipts)){[void][IO.Directory]::CreateDirectory($dir)}
 Put (Join-Path $kit 'INTERLINKED.cmd') '@echo OLD'
 if($DesktopInstalled -or $V2Baseline -or $V3Baseline -or $V4Baseline){
  Put (Join-Path $kit 'INTERLINKED.cmd') '@echo DESKTOP PREVIOUS'
  Put (Join-Path $kit 'INTERLINKED-Browser.cmd') '@echo OLD'
 }
 Put (Join-Path $kit 'INTERLINKED-App/interlinked_gui.py') 'old backend'
 $oldFiles=[ordered]@{}
 foreach($name in @('interlinked_gui.py','web/index.html','web/app.js','web/style.css')){
  $path=Join-Path $kit ('INTERLINKED-App/'+$name);if(-not(Test-Path -LiteralPath $path)){Put $path ('fixture '+$name)};$oldFiles[$name]=Digest $path
 }
 if($V2Baseline -or $V3Baseline -or $V4Baseline){
  foreach($name in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py')){
   $path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  }
 }
 if($V3Baseline -or $V4Baseline){
  foreach($name in @('interlinked_master.py','master_hook.py','master_bridge.py')){
   $path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
  }
 }
 if($V4Baseline){
  $name='interlinked_jobs.py';$path=Join-Path $kit ('INTERLINKED-App/'+$name);Put $path ('old fixture '+$name);$oldFiles[$name]=Digest $path
 }
 Put (Join-Path $kit 'MAGIX-Media-Audit.json') '{"fixture":true}'
 $oldSeal=[ordered]@{format='interlinked-gui-checksums-v1';files=$oldFiles;auditSha256=Digest (Join-Path $kit 'MAGIX-Media-Audit.json')}
 if($V2Baseline){$oldSeal.format='interlinked-gui-checksums-v2'}
 if($V3Baseline){$oldSeal.format='interlinked-gui-checksums-v3'}
 if($V4Baseline){$oldSeal.format='interlinked-gui-checksums-v4'}
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
 $newFiles=[ordered]@{}
 foreach($name in @('interlinked_gui.py','interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','web/index.html','web/app.js','web/style.css')){
  $newFiles[$name]=Digest (Join-Path $source ('INTERLINKED-App/'+$name))
 }
 Json (Join-Path $source 'INTERLINKED-App/GUI-CHECKSUMS.json') @{format='interlinked-gui-checksums-v4';files=$newFiles;auditSha256=$oldSeal.auditSha256}
 $desktopFiles=[ordered]@{};foreach($name in $allowed | Where-Object {$_ -like 'INTERLINKED-Desktop/*' -and $_ -notlike '*/DESKTOP-CHECKSUMS.json'}){$desktopFiles[$name.Substring(20)]=Digest (Join-Path $source $name)}
 Json (Join-Path $source 'INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json') @{format='interlinked-desktop-checksums-v1';files=$desktopFiles}
 if($DesktopInstalled -or $V2Baseline -or $V3Baseline -or $V4Baseline){
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
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 25) 'Complete MASTER and MSI Jobs package publishes and readback passes'
$receipt=Get-Content $result.receipt -Raw|ConvertFrom-Json
Check ($receipt.files[-1].path -ceq 'INTERLINKED.cmd') 'New desktop launcher publishes last'
$wrapperRow=$receipt.files|Where-Object path -ceq 'INTERLINKED.cmd'
Check ((Digest $wrapperRow.backup) -ceq $before) 'Prior installed launcher is preserved byte exactly'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd')) -ceq $before) 'Browser recovery launcher preserves previous workflow'
Check (-not $receipt.projectFilesWritten -and -not $receipt.workspaceStateWritten) 'Receipt distinguishes app publication from workspace changes'
Check ((Get-Content (Join-Path $f.Context.Kit 'MAGIX-Workspace-State/multi-binding.json') -Raw|ConvertFrom-Json).status -ceq 'UNBOUND') 'Publication retains workspace binding'

$f=Fixture -DesktopInstalled;$browserBefore=Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd');$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED') 'Installed desktop v1 GUI upgrades to v4 MASTER and MSI Jobs seal'
Check ((Digest (Join-Path $f.Context.Kit 'INTERLINKED-Browser.cmd')) -ceq $browserBefore) 'Desktop upgrade preserves existing browser recovery bytes rather than desktop wrapper'
$f=Fixture -V2Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 25) 'Existing v2 seven-file GUI upgrades with all companion hashes checked'
$installed=Get-Content (Join-Path $f.Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json') -Raw|ConvertFrom-Json
Check ($installed.format -ceq 'interlinked-gui-checksums-v4' -and @($installed.files.PSObject.Properties).Count -eq 11) 'Installed MASTER and MSI Jobs seal covers exactly eleven app and web files'
$f=Fixture -V3Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 25) 'Existing v3 MASTER baseline upgrades with exact companion hashes checked'
$f=Fixture -V4Baseline;$result=Invoke $f -Apply
Check ($result.status -ceq 'DESKTOP_PUBLISHED_HASH_VERIFIED' -and $result.files -eq 25) 'Existing v4 MSI Jobs baseline upgrades with exact companion hashes checked'

foreach($companion in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py')){
 $f=Fixture;Put (Join-Path $f.Source ('INTERLINKED-App/'+$companion)) 'tampered companion'
 Reject {Invoke $f -Apply} ('Changed reviewed companion source refused: '+$companion)
 $f=Fixture -V4Baseline;Put (Join-Path $f.Context.Kit ('INTERLINKED-App/'+$companion)) 'tampered installed companion'
 Reject {Invoke $f -Apply} ('Changed installed companion seal refused: '+$companion)
}
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.files.PSObject.Properties.Remove('interlinked_mw5.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective seal missing a companion is refused'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v1';foreach($name in @('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py','interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py')){$seal.files.PSObject.Properties.Remove($name)};Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective legacy four-file GUI seal cannot publish MASTER'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v2';foreach($name in @('interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py')){$seal.files.PSObject.Properties.Remove($name)};Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective seven-file v2 seal cannot omit MASTER modules'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.format='interlinked-gui-checksums-v3';$seal.files.PSObject.Properties.Remove('interlinked_jobs.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'A prospective ten-file v3 seal cannot omit MSI Jobs'
$f=Fixture;$sealPath=Join-Path $f.Source 'INTERLINKED-App/GUI-CHECKSUMS.json';$seal=Get-Content $sealPath -Raw|ConvertFrom-Json
$seal.files.PSObject.Properties.Remove('interlinked_jobs.py');Json $sealPath $seal;Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective v4 seal missing MSI Jobs is refused'
$f=Fixture;Put (Join-Path $f.Source 'INTERLINKED-App/interlinked_jobs.py') 'different reviewed MSI Jobs source';Refresh-Plan $f
Reject {Invoke $f -Apply} 'Prospective MSI Jobs seal mismatch is refused even with a reviewed plan hash'
$f=Fixture;$plan=Get-Content $f.Plan -Raw|ConvertFrom-Json;$plan.files=@($plan.files | Where-Object path -cne 'INTERLINKED-App/interlinked_jobs.py');Json $f.Plan $plan
Reject {Invoke $f -Apply} 'Publication plan missing MSI Jobs is refused'
$f=Fixture -V4Baseline;[IO.File]::Delete((Join-Path $f.Context.Kit 'INTERLINKED-App/interlinked_jobs.py'))
Reject {Invoke $f -Apply} 'Missing installed v4 MSI Jobs module is refused'
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
Json (Join-Path $root 'publication-test-results.json') @{status='PASS';checks=@($checks);count=$checks.Count;scope='Synthetic files, locks, seals and publication only; no production update or MAGIX launched.'}
[pscustomobject]@{status='PASS';checks=$checks.Count;root=$root}|ConvertTo-Json
