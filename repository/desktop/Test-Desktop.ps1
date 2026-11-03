[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$FixtureDirectory,
    [string]$ApplicationSource,
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
$package = (Resolve-Path -LiteralPath $PackageDirectory).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$fixture = [IO.Path]::GetFullPath($FixtureDirectory)
if (Test-Path -LiteralPath $fixture) { throw 'Use a fresh absent synthetic fixture directory.' }
if ($fixture.StartsWith('C:\Users\Jude\Documents\MSI-Project-Library',[StringComparison]::OrdinalIgnoreCase) -or $fixture.StartsWith((Join-Path $env:LOCALAPPDATA 'ProjectLibrary'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Synthetic tests must stay outside the production kit and cache.' }
$exe = Join-Path $package 'INTERLINKED.exe'
$manifest = Get-Content -LiteralPath (Join-Path $package 'DESKTOP-CHECKSUMS.json') -Raw | ConvertFrom-Json
if ($manifest.format -cne 'interlinked-desktop-checksums-v1') { throw 'Unknown desktop package seal.' }
$required = @('INTERLINKED.exe','INTERLINKED.exe.config','INTERLINKED.Master.Worker.exe','INTERLINKED.Master.Worker.exe.config','Violet.png','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt','WebView2-NOTICE.txt')
$members = @($manifest.files.PSObject.Properties.Name)
if ($members.Count -ne $required.Count -or @($required | Where-Object {$_ -cnotin $members}).Count -ne 0) { throw 'The desktop seal must contain the exact ten reviewed payload files.' }
$actual = @(Get-ChildItem -LiteralPath $package -Force | Select-Object -ExpandProperty Name)
if ($actual.Count -ne 11 -or @($actual | Where-Object {$_ -cnotin ($required + 'DESKTOP-CHECKSUMS.json')}).Count -ne 0) { throw 'Unexpected desktop package contents.' }
foreach ($file in $manifest.files.PSObject.Properties) {
    if ($file.Name -cne [IO.Path]::GetFileName($file.Name)) { throw 'Invalid package member.' }
    if ($file.Value -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid desktop package hash.' }
    $member = Get-Item -LiteralPath (Join-Path $package $file.Name) -Force
    if ($member.PSIsContainer -or ($member.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or ([uint32]$member.Attributes -band 0x441000) -ne 0) { throw 'Desktop package member must be an ordinary local file.' }
    if ((Get-FileHash -LiteralPath (Join-Path $package $file.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.Value) { throw 'Desktop package changed.' }
}
Add-Type -AssemblyName System.Drawing
$violet = Join-Path $package 'Violet.png'
$worker = Join-Path $package 'INTERLINKED.Master.Worker.exe'
$image = [Drawing.Image]::FromFile($violet)
try { if ($image.Width -ne 1536 -or $image.Height -ne 2288) { throw 'Desktop package requires the reviewed v2 Violet atlas dimensions.' } } finally { $image.Dispose() }
if ($PreflightOnly) { [pscustomobject]@{Status='PACKAGE_PREFLIGHT_PASS';Files=$required.Count;Scope='Exact package seal and atlas dimensions; no executable or fixture was started'} | ConvertTo-Json; return }
New-Item -ItemType Directory -Path $fixture | Out-Null
$app = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\app\interlinked_gui.py'))
if ($ApplicationSource) { $app = (Resolve-Path -LiteralPath $ApplicationSource).Path }
$receipt = Join-Path $fixture 'smoke.json'
$arguments = @('--demo','--smoke-test','--app',('"'+$app+'"'),'--python',('"'+$pythonPath+'"'),'--demo-state',('"'+(Join-Path $fixture 'state')+'"'),'--receipt',('"'+$receipt+'"'),'--master-worker',('"'+$worker+'"'),'--master-violet',('"'+$violet+'"'))
$process = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(120000)) { throw 'Synthetic test timed out; its processes were preserved, never terminated.' }
if ($process.ExitCode -ne 0) { throw "Desktop smoke test returned $($process.ExitCode). Inspect the private fixture receipt." }
$result = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
if ($result.format -cne 'interlinked-desktop-smoke-v1' -or $result.status -cne 'PASS' -or -not $result.demoOnly -or $result.reportDownloads -ne 2) { throw 'Desktop smoke test did not pass.' }
foreach ($name in @('catalogLoaded','singleInstanceActivation','localNavigationOnly','externalNavigationBlocked','auditWindow','busyCloseRefused','msiJobsPanelLoaded','msiJobsLogsRendered','msiJobsLogStreamSwitching','msiJobsRefreshPreservesSession','msiJobsLibraryReturnPreservesSession','rendererRecoveryPreservesSession','doneFinishesSession','explicitSave','favoritePersistence','mixedProjectFilters','genericSessionProtected','genericSessionFinished','mw5Readiness','mw5StagePreview','mw5StagingProtected','mw5EditorSession','mw5CollectPreview','mw5CollectionFinishesSession','mw5ExplicitSave','hostObjectsDisabled','webMessagesRestricted','auditWebMessagesDisabled','aiWorkspaceLoaded','aiModelControls','aiWorkerControls','devToolsDisabled','inPrivateProfile','gracefulShutdown','serviceExited','busyCloseDoesNotWriteWindowPreferences','windowPreferenceRoundtrip','windowPreferenceVisibleBounds','windowPreferenceOwnership','windowPreferenceInvalidDataPreserved','windowPreferencesSavedOnClose')) { if ($result.$name -ne $true) { throw "Missing or failed desktop check: $name" } }
foreach ($extension in @('json','csv')) { $report = Join-Path $fixture ('INTERLINKED-missing-media-demo-project-1.'+$extension); if (-not (Test-Path -LiteralPath $report -PathType Leaf) -or (Get-Item -LiteralPath $report).Length -lt 30) { throw 'Synthetic export missing.' } }
[pscustomobject]@{Status='PASS';Scope='Real WebView2 mixed-project hub, read-only MSI Jobs, and AI workspace controls with isolated synthetic backend only';Receipt=$receipt;Runtime=$result.runtime;Checks=43} | ConvertTo-Json
