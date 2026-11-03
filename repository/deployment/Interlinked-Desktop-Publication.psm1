# Internal publication engine. The production entry point supplies fixed identity,
# path and provenance pins; fixture tests supply only isolated synthetic roots.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'

function Get-DesktopDigest([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-DesktopOrdinary([string]$Path,[switch]$AllowMissing) {
    $full=[IO.Path]::GetFullPath($Path)
    $walk=$full
    while($walk){
        if(Test-Path -LiteralPath $walk){
            $item=Get-Item -LiteralPath $walk -Force
            # Reject junctions, symbolic links, offline and recall/cloud paths.
            if(([long]$item.Attributes -band 0x441400) -ne 0){throw ('Link/cloud path refused: '+$walk)}
        } elseif(-not $AllowMissing){throw ('Required path is absent: '+$walk)}
        $parent=[IO.Path]::GetDirectoryName($walk)
        if($parent -eq $walk){break};$walk=$parent
    }
}

function Initialize-DesktopNative {
    if('InterlinkedDesktopPublicationNative' -as [type]){return}
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class InterlinkedDesktopPublicationNative {
  [StructLayout(LayoutKind.Sequential)] public struct Info {
    public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh,
      WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
  }
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
  public static uint Links(SafeFileHandle handle) {
    Info info;
    if(!GetFileInformationByHandle(handle, out info))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    return info.Links;
  }
}
'@
}

function Read-DesktopBytes([string]$Path,[switch]$SingleLink) {
    Assert-DesktopOrdinary $Path
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if($SingleLink){
            Initialize-DesktopNative
            if([InterlinkedDesktopPublicationNative]::Links($stream.SafeFileHandle) -ne 1){throw ('Hardlinked publication file refused: '+$Path)}
        }
        if($stream.Length -gt 33554432){throw 'Publication file exceeds 32 MiB.'}
        $bytes=New-Object byte[] ([int]$stream.Length)
        $offset=0
        while($offset -lt $bytes.Length){$n=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($n -eq 0){throw 'Short publication read.'};$offset+=$n}
        return ,$bytes
    } finally {$stream.Dispose()}
}

function Get-DesktopBytesDigest([byte[]]$Bytes) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}
    finally {$sha.Dispose()}
}

function Read-DesktopJson([string]$Path,[switch]$SingleLink) {
    $bytes=Read-DesktopBytes $Path -SingleLink:$SingleLink
    $text=[Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xfeff)
    $text | ConvertFrom-Json
}

function Join-DesktopSafe([string]$Root,[string]$Relative) {
    if(-not $Relative -or $Relative.Contains('\') -or $Relative.Contains(':') -or $Relative.StartsWith('/') -or ($Relative.Split('/') | Where-Object {$_ -in @('','..','.')})){
        throw 'Unsafe manifest relative path.'
    }
    $path=[IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if(-not $path.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest path escaped root.'}
    $path
}

function Assert-DesktopObjectKeys($Object,[string[]]$Keys) {
    $actual=@($Object.PSObject.Properties.Name | Sort-Object)
    if(($actual -join '|') -cne (($Keys | Sort-Object) -join '|')){throw 'Metadata fields differ from expected schema.'}
}

function Assert-DesktopGuiSeal($Context,$Seal,[hashtable]$Prospective) {
    Assert-DesktopObjectKeys $Seal @('format','files','auditSha256')
    $names=@('interlinked_gui.py','web/index.html','web/app.js','web/style.css')
    if($Prospective -and $Seal.format -cne 'interlinked-gui-checksums-v10'){
        throw 'The history release requires an exact twenty-six-file v10 GUI seal.'
    }
    if($Seal.format -cin @('interlinked-gui-checksums-v2','interlinked-gui-checksums-v3','interlinked-gui-checksums-v4','interlinked-gui-checksums-v5','interlinked-gui-checksums-v6','interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){
        $names+=@('interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py')
        if($Seal.format -cin @('interlinked-gui-checksums-v3','interlinked-gui-checksums-v4','interlinked-gui-checksums-v5','interlinked-gui-checksums-v6','interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_master.py','master_hook.py','master_bridge.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v4','interlinked-gui-checksums-v5','interlinked-gui-checksums-v6','interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_jobs.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v5','interlinked-gui-checksums-v6','interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_buddy.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v6','interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_stats.py','interlinked_stats_process.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v7','interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_configuration.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v8','interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_ai.py','interlinked_ai_node.py','interlinked_ai_workspaces.py')}
        if($Seal.format -cin @('interlinked-gui-checksums-v9','interlinked-gui-checksums-v10')){$names+=@('interlinked_ai_bridge.py','interlinked_ai_ipc.py','ai_bridge.py')}
        if($Seal.format -ceq 'interlinked-gui-checksums-v10'){$names+=@('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py')}
    } elseif($Seal.format -ceq 'interlinked-gui-checksums-v1'){
    } else {throw 'Unknown GUI seal format.'}
    Assert-DesktopObjectKeys $Seal.files $names
    foreach($name in $names){
        $key='INTERLINKED-App/'+$name
        $actual=if($Prospective -and $Prospective.ContainsKey($key)){$Prospective[$key].sha256}else{
            $path=Join-DesktopSafe $Context.Kit $key;Assert-DesktopOrdinary $path;Get-DesktopDigest $path
        }
        if($actual -cne $Seal.files.$name){throw ('GUI seal differs: '+$name)}
    }
    if($Seal.auditSha256){
        $path=Join-Path $Context.Kit 'MAGIX-Media-Audit.json';Assert-DesktopOrdinary $path
        if((Get-DesktopDigest $path) -cne $Seal.auditSha256){throw 'Private audit seal differs.'}
    }
}

function Get-DesktopLauncherMigration($Context) {
    $property=$Context.PSObject.Properties['LauncherMigration']
    if(-not $property -or $null -eq $property.Value){return $null}
    $migration=$property.Value
    Assert-DesktopObjectKeys $migration @('path','beforeSha256','afterSha256','beforeBuddySha256','afterBuddySha256')
    if($migration.path -cne 'magix_projects.py'){throw 'Only the reviewed MAGIX launcher can migrate.'}
    foreach($key in @('beforeSha256','afterSha256','beforeBuddySha256','afterBuddySha256')){
        if($migration.$key -cnotmatch '^[a-f0-9]{64}$'){throw 'Launcher migration requires fixed reviewed SHA-256 values.'}
    }
    if($migration.beforeSha256 -ceq $migration.afterSha256 -or $migration.beforeBuddySha256 -ceq $migration.afterBuddySha256){throw 'Launcher migration must change the reviewed Buddy pin.'}
    if(-not $Context.Pins.ContainsKey('magix_projects.py') -or $Context.Pins['magix_projects.py'] -cne $migration.beforeSha256){throw 'Launcher migration baseline differs from the accepted dependency pin.'}
    return $migration
}

function Get-DesktopPythonPin([byte[]]$Bytes,[string]$Name) {
    # Strict UTF-8 decoding retains BOMs and line endings. The accepted assignment
    # is a single unindented literal; no Python code is executed during review.
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($Bytes)
    $pattern='(?m)^'+[regex]::Escape($Name)+'=([''"])([a-f0-9]{64})\1\r?$'
    $matches=[regex]::Matches($text,$pattern)
    if($matches.Count -ne 1){throw ('Expected exactly one literal Python pin: '+$Name)}
    return [pscustomobject]@{text=$text;value=$matches[0].Groups[2].Value;index=$matches[0].Groups[2].Index}
}

function Assert-DesktopLauncherChain($Migration,[byte[]]$Launcher,[byte[]]$Buddy,[byte[]]$Gui) {
    if((Get-DesktopBytesDigest $Launcher) -cne $Migration.afterSha256 -or (Get-DesktopBytesDigest $Buddy) -cne $Migration.afterBuddySha256){throw 'Migrated launcher or Buddy companion differs from reviewed bytes.'}
    Assert-DesktopBuddyChain $Launcher $Buddy $Gui
}

function Assert-DesktopBuddyChain([byte[]]$Launcher,[byte[]]$Buddy,[byte[]]$Gui) {
    if((Get-DesktopPythonPin $Launcher 'BUDDY_SHA').value -cne (Get-DesktopBytesDigest $Buddy)){throw 'Prospective launcher does not pin the exact Buddy companion.'}
    if((Get-DesktopPythonPin $Gui 'MAGIX_SHA').value -cne (Get-DesktopBytesDigest $Launcher)){throw 'Prospective GUI does not pin the exact launcher.'}
}

function Assert-DesktopLauncherMigration($Context,$Migration,[hashtable]$Rows,[hashtable]$Buffers) {
    # A widened caller allowlist must not authorize replacement of other pins.
    foreach($name in $Context.Pins.Keys){
        if($Rows.ContainsKey($name) -and ($name -cne 'magix_projects.py' -or -not $Migration)){throw 'Pinned dependency replacement is not authorized.'}
    }
    if(-not $Migration){
        if($Rows.ContainsKey('magix_projects.py')){throw 'Launcher replacement requires an exact reviewed migration.'}
        return
    }
    $name='magix_projects.py'
    if(-not $Rows.ContainsKey($name)){throw 'Reviewed launcher migration is missing from the publication plan.'}
    if($Rows[$name].beforeSha256 -cne $Migration.beforeSha256 -or $Rows[$name].sha256 -cne $Migration.afterSha256){throw 'Launcher plan differs from reviewed before/after hashes.'}
    $before=Read-DesktopBytes (Join-Path $Context.Kit $name) -SingleLink
    if((Get-DesktopBytesDigest $before) -cne $Migration.beforeSha256){throw 'Installed launcher differs from migration baseline.'}
    $pin=Get-DesktopPythonPin $before 'BUDDY_SHA'
    if($pin.value -cne $Migration.beforeBuddySha256){throw 'Installed launcher Buddy pin differs from migration baseline.'}
    $oldBuddy=Read-DesktopBytes (Join-Path $Context.Kit 'INTERLINKED-App/interlinked_buddy.py') -SingleLink
    if((Get-DesktopBytesDigest $oldBuddy) -cne $Migration.beforeBuddySha256){throw 'Installed Buddy companion differs from the accepted launcher pin.'}
    $expectedText=$pin.text.Remove($pin.index,64).Insert($pin.index,$Migration.afterBuddySha256)
    $expectedBytes=[Text.UTF8Encoding]::new($false,$true).GetBytes($expectedText)
    if([Convert]::ToBase64String($expectedBytes) -cne [Convert]::ToBase64String($Buffers[$name])){throw 'Launcher migration changes bytes beyond the single BUDDY_SHA value.'}
    Assert-DesktopLauncherChain $Migration $Buffers[$name] $Buffers['INTERLINKED-App/interlinked_buddy.py'] $Buffers['INTERLINKED-App/interlinked_gui.py']
}

function Assert-DesktopBaseline($Context,[switch]$AfterMigration) {
    $migration=Get-DesktopLauncherMigration $Context
    foreach($name in $Context.Pins.Keys){
        $path=Join-DesktopSafe $Context.Kit $name
        Assert-DesktopOrdinary $path
        $expected=if($AfterMigration -and $migration -and $name -ceq 'magix_projects.py'){$migration.afterSha256}else{$Context.Pins[$name]}
        if((Get-DesktopDigest $path) -cne $expected){throw ('Accepted dependency differs: '+$name)}
    }
    $client=Join-Path $Context.Kit 'Project-Library-Client'
    $sealPath=Join-Path $client 'HELPER-CHECKSUMS.json'
    Assert-DesktopOrdinary $sealPath
    if((Get-DesktopDigest $sealPath) -cne $Context.ClientSealSha256){throw 'Original client seal differs.'}
    $seal=Read-DesktopJson $sealPath
    if($seal.format -cne 'project-library-client-checksums-v1' -or @($seal.files).Count -ne $Context.ClientFileCount){throw 'Original client seal schema/count differs.'}
    $seen=@{}
    foreach($row in $seal.files){
        $path=Join-DesktopSafe $client $row.path
        Assert-DesktopOrdinary $path
        if($seen.ContainsKey($row.path)){throw 'Duplicate sealed client path.'};$seen[$row.path]=$true
        if((Get-Item -LiteralPath $path).Length -ne $row.bytes -or (Get-DesktopDigest $path) -cne $row.sha256){throw ('Original sealed client differs: '+$row.path)}
    }
    Assert-DesktopGuiSeal $Context (Read-DesktopJson (Join-Path $Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json')) $null
    if($AfterMigration){
        $launcher=Read-DesktopBytes (Join-Path $Context.Kit 'magix_projects.py') -SingleLink
        $buddy=Read-DesktopBytes (Join-Path $Context.Kit 'INTERLINKED-App/interlinked_buddy.py') -SingleLink
        $gui=Read-DesktopBytes (Join-Path $Context.Kit 'INTERLINKED-App/interlinked_gui.py') -SingleLink
        if($migration){Assert-DesktopLauncherChain $migration $launcher $buddy $gui}
        else{Assert-DesktopBuddyChain $launcher $buddy $gui}
    }
}

function Assert-DesktopIdle($Context) {
    $stats=Join-Path $Context.Kit 'INTERLINKED-GUI-State/stats/session.json'
    if(Test-Path -LiteralPath $stats){
        $session=Read-DesktopJson $stats -SingleLink
        if($session.format -cne 'interlinked-stats-session-v1' -or $session.status -cne 'IDLE'){
            throw 'Stop Stats and reconcile its managed session before updating INTERLINKED.'
        }
    }
    foreach($name in @('binding.json','multi-binding.json')){
        $binding=Read-DesktopJson (Join-Path $Context.Kit ('MAGIX-Workspace-State/'+$name))
        if($binding.status -cne 'UNBOUND'){throw ('Finish MAGIX and confirm Done before updating: '+$name)}
    }
    $mw5=Join-Path $Context.Kit 'INTERLINKED-GUI-State/mw5'
    if(Test-Path -LiteralPath $mw5){
        Assert-DesktopOrdinary $mw5
        $journal=Join-Path $mw5 'session.json'
        # An existing but incomplete adapter state is not an idle session.
        $session=Read-DesktopJson $journal -SingleLink
        if($session.format -cne 'interlinked-mw5-session-v1' -or $session.status -cne 'IDLE'){
            throw 'Finish MW5 collection and its managed session before updating INTERLINKED.'
        }
    }
    $folder=Join-Path $Context.Kit 'INTERLINKED-GUI-State/folder-session.json'
    if(Test-Path -LiteralPath $folder){
        $session=Read-DesktopJson $folder -SingleLink
        if($session.format -cne 'interlinked-folder-session-v1' -or $session.status -cne 'IDLE'){
            throw 'Finish the managed folder session before updating INTERLINKED.'
        }
    }
    # Inspection failure is a blocker; an empty/partial process view must not be
    # used to replace the authoritative workflow and GUI-instance locks.
    $processes=@(Get-Process -ErrorAction Stop)
    if(@($processes | Where-Object {$_.ProcessName -match '^(videodeluxe|movieeditpro|video_pro_x|magix)' }).Count){throw 'Close MAGIX before updating INTERLINKED.'}
    if(@($processes | Where-Object {$_.ProcessName -match '^(ue4editor|unrealeditor|mechwarrior5editor)([-.]|$)' }).Count){throw 'Close MW5/Unreal Editor before updating INTERLINKED.'}
    if(@($processes | Where-Object {$_.ProcessName -ceq 'INTERLINKED'}).Count){throw 'Exit the INTERLINKED desktop host before updating.'}
    if(@($processes | Where-Object {$_.ProcessName -ceq 'INTERLINKED.Master.Worker'}).Count){throw 'Disconnect and exit the MASTER display worker before updating.'}
}

function Invoke-DesktopPublication {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Context,
          [Parameter(Mandatory=$true)][string]$ManifestPath,
          [Parameter(Mandatory=$true)][string]$ManifestSha256,
          [Parameter(Mandatory=$true)][string]$ReceiptDirectory,
          [switch]$Apply)
    $run=[guid]::NewGuid().ToString('N')
    $locks=New-Object System.Collections.ArrayList
    $receipt=[ordered]@{format='interlinked-desktop-publication-receipt-v1';status='PREFLIGHT';startedUtc=[DateTime]::UtcNow.ToString('o');runId=$run;apply=[bool]$Apply;files=@();projectFilesWritten=$false;workspaceStateWritten=$false;failure=$null}
    $receiptRoot=[IO.Path]::GetFullPath($ReceiptDirectory)
    if(-not $receiptRoot.StartsWith($Context.Workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Receipts must stay within the source workspace.'}
    Assert-DesktopOrdinary $receiptRoot
    $receiptPath=Join-Path $receiptRoot ('desktop-publication-'+$run+'.json')
    function Save-Receipt {$receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding utf8}
    try {
        Assert-DesktopOrdinary $Context.Kit
        $migration=Get-DesktopLauncherMigration $Context
        $planBytes=Read-DesktopBytes $ManifestPath -SingleLink
        if((Get-DesktopBytesDigest $planBytes) -cne $ManifestSha256.ToLowerInvariant()){throw 'Reviewed publication plan hash differs.'}
        $plan=[Text.Encoding]::UTF8.GetString($planBytes).TrimStart([char]0xfeff) | ConvertFrom-Json
        Assert-DesktopObjectKeys $plan @('format','files')
        if($plan.format -cne 'interlinked-desktop-publication-v1'){throw 'Unknown publication plan format.'}
        $rows=@{};$buffers=@{}
        foreach($row in $plan.files){
            Assert-DesktopObjectKeys $row @('path','source','sha256','beforeSha256')
            if($row.path -cnotin $Context.Allowed -or $rows.ContainsKey($row.path)){throw 'Unknown or duplicate publication target.'}
            if($row.sha256 -cnotmatch '^[a-f0-9]{64}$' -or ($row.beforeSha256 -and $row.beforeSha256 -cnotmatch '^[a-f0-9]{64}$')){throw 'Invalid publication SHA-256.'}
            $source=[IO.Path]::GetFullPath($row.source)
            if(-not $source.StartsWith($Context.Workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication source is outside workspace.'}
            $bytes=Read-DesktopBytes $source -SingleLink
            if((Get-DesktopBytesDigest $bytes) -cne $row.sha256){throw ('Reviewed source differs: '+$row.path)}
            $rows[$row.path]=$row;$buffers[$row.path]=$bytes
        }
        if($rows.Count -ne $Context.Allowed.Count){throw 'Incomplete desktop publication plan.'}
        # Acquire the same process-held locks used by the accepted GUI/launcher.
        # Open existing files only; this updater never truncates or creates locks.
        foreach($name in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){
            $path=Join-DesktopSafe $Context.Kit $name;Assert-DesktopOrdinary $path
            try {$handle=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)}
            catch {throw ('Active or unavailable session lock; finish MAGIX/Done and exit INTERLINKED: '+$name)}
            [void]$locks.Add($handle)
        }
        Assert-DesktopIdle $Context
        Assert-DesktopBaseline $Context
        Assert-DesktopLauncherMigration $Context $migration $rows $buffers
        if($migration){$receipt.launcherMigration=$migration}
        foreach($row in $rows.Values){
            $target=Join-DesktopSafe $Context.Kit $row.path
            Assert-DesktopOrdinary $target -AllowMissing
            if(Test-Path -LiteralPath $target){
                $before=Read-DesktopBytes $target -SingleLink
                if(-not $row.beforeSha256 -or (Get-DesktopBytesDigest $before) -cne $row.beforeSha256){throw ('Existing target differs: '+$row.path)}
            } elseif($row.beforeSha256){throw ('Expected existing target is absent: '+$row.path)}
        }
        $browserBefore=$rows['INTERLINKED-Browser.cmd'].beforeSha256
        if($browserBefore){
            if($rows['INTERLINKED-Browser.cmd'].sha256 -cne $browserBefore){throw 'The existing browser recovery wrapper must remain byte exact.'}
        } elseif($rows['INTERLINKED-Browser.cmd'].sha256 -cne $rows['INTERLINKED.cmd'].beforeSha256){
            throw 'The first browser recovery wrapper must preserve the exact previous launcher.'
        }
        $newGui=[Text.Encoding]::UTF8.GetString($buffers['INTERLINKED-App/GUI-CHECKSUMS.json']).TrimStart([char]0xfeff) | ConvertFrom-Json
        $oldGui=Read-DesktopJson (Join-Path $Context.Kit 'INTERLINKED-App/GUI-CHECKSUMS.json')
        if($newGui.auditSha256 -cne $oldGui.auditSha256){throw 'Desktop update cannot replace the private audit.'}
        Assert-DesktopGuiSeal $Context $newGui $rows
        $prospectiveLauncher=if($migration){$buffers['magix_projects.py']}else{Read-DesktopBytes (Join-Path $Context.Kit 'magix_projects.py') -SingleLink}
        Assert-DesktopBuddyChain $prospectiveLauncher $buffers['INTERLINKED-App/interlinked_buddy.py'] $buffers['INTERLINKED-App/interlinked_gui.py']
        $desktopSeal=[Text.Encoding]::UTF8.GetString($buffers['INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json']).TrimStart([char]0xfeff) | ConvertFrom-Json
        if($desktopSeal.format -cne 'interlinked-desktop-checksums-v1'){throw 'Unknown desktop package seal.'}
        $desktopNames=@($Context.Allowed | Where-Object {$_ -like 'INTERLINKED-Desktop/*' -and $_ -cne 'INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json'} | ForEach-Object {$_.Substring(20)})
        Assert-DesktopObjectKeys $desktopSeal.files $desktopNames
        foreach($name in $desktopNames){if($desktopSeal.files.$name -cne $rows['INTERLINKED-Desktop/'+$name].sha256){throw ('Desktop seal differs: '+$name)}}
        $receipt.status='PREFLIGHT_PASSED';Save-Receipt
        if(-not $Apply){return [pscustomobject]@{status='PREFLIGHT_PASSED';receipt=$receiptPath;files=$rows.Count}}
        $desktop=Join-Path $Context.Kit 'INTERLINKED-Desktop'
        if(-not (Test-Path -LiteralPath $desktop)){[void][IO.Directory]::CreateDirectory($desktop)}
        Assert-DesktopOrdinary $desktop
        foreach($row in ($rows.Values | Sort-Object @{Expression={if($_.path -ceq 'INTERLINKED.cmd'){3}elseif($_.path -ceq 'INTERLINKED-App/GUI-CHECKSUMS.json'){2}elseif($_.path -ceq 'INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json'){1}else{0}}},path)){
            $target=Join-DesktopSafe $Context.Kit $row.path
            Assert-DesktopOrdinary $target -AllowMissing
            if($row.beforeSha256){
                $before=Read-DesktopBytes $target -SingleLink
                if((Get-DesktopBytesDigest $before) -cne $row.beforeSha256){throw 'Publication target changed after preflight.'}
            }elseif(Test-Path -LiteralPath $target){throw 'New publication target appeared after preflight.'}
            if($row.sha256 -ceq $row.beforeSha256){$receipt.files+=@([ordered]@{path=$row.path;sha256=$row.sha256;backup=$null;unchanged=$true});Save-Receipt;continue}
            $temporary=$target+'.pending-desktop-'+$run
            $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try {$bytes=$buffers[$row.path];$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
            if((Get-DesktopDigest $temporary) -cne $row.sha256){throw 'Staged publication bytes differ.'}
            $backup=$null
            if($row.beforeSha256){
                $backup=$target+'.before-desktop-'+$run
                [IO.File]::Replace($temporary,$target,$backup)
                if((Get-DesktopDigest $backup) -cne $row.beforeSha256){throw 'Prior file was not preserved exactly.'}
            }else{[IO.File]::Move($temporary,$target)}
            if((Get-DesktopDigest $target) -cne $row.sha256){throw 'Installed publication bytes differ.'}
            $receipt.files+=@([ordered]@{path=$row.path;sha256=$row.sha256;backup=$backup;unchanged=$false});Save-Receipt
        }
        Assert-DesktopBaseline $Context -AfterMigration
        Assert-DesktopIdle $Context
        $receipt.status='DESKTOP_PUBLISHED_HASH_VERIFIED';$receipt.acceptedLauncherUnchanged=(-not $migration)
        Save-Receipt
        return [pscustomobject]@{status=$receipt.status;receipt=$receiptPath;files=$receipt.files.Count}
    } catch {$receipt.status='PUBLICATION_STOPPED';$receipt.failure=$_.Exception.Message;throw}
    finally {foreach($handle in $locks){$handle.Dispose()};$receipt.finishedUtc=[DateTime]::UtcNow.ToString('o');Save-Receipt}
}

Export-ModuleMember -Function Invoke-DesktopPublication
