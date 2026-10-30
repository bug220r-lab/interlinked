# Narrow internal engine: verify the full installed baseline and reviewed plan,
# but replace only the cooler worker and its desktop seal. No GUI migration.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'

function Invoke-CoolerPublication {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Context,
          [Parameter(Mandatory=$true)][string]$ManifestPath,
          [Parameter(Mandatory=$true)][string]$ManifestSha256,
          [Parameter(Mandatory=$true)][string]$ReceiptDirectory,
          [switch]$Apply)
    $guardPath=Join-Path $PSScriptRoot 'Interlinked-Desktop-Publication.psm1'
    $walk=[IO.Path]::GetFullPath($guardPath)
    while($walk){
        $item=Get-Item -LiteralPath $walk -Force
        if(([long]$item.Attributes -band 0x441400)-ne 0){throw 'The guard module and ancestors must be ordinary local paths.'}
        $parent=[IO.Path]::GetDirectoryName($walk);if($parent-eq $walk){break};$walk=$parent
    }
    if(-not('InterlinkedCoolerModuleGuard' -as [type])){
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class InterlinkedCoolerModuleGuard {
  [StructLayout(LayoutKind.Sequential)] public struct Info {
    public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh,
      WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
  }
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Info info);
  public static uint Links(SafeFileHandle handle) {
    Info info;
    if(!GetFileInformationByHandle(handle,out info))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    return info.Links;
  }
}
'@
    }
    $guardStream=[IO.File]::Open($guardPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if([InterlinkedCoolerModuleGuard]::Links($guardStream.SafeFileHandle)-ne 1){throw 'Hardlinked publication guard module refused.'}
        # Preserve an already imported instance so isolated fixtures can inject
        # only their module-scoped process enumeration. The public entrypoint
        # supplies no injection mechanism or caller-controlled context.
        $guardModule=Import-Module $guardPath -PassThru
        & $guardModule {
            param($Context,$ManifestPath,$ManifestSha256,$ReceiptDirectory,$Apply)
            $allowed=@('INTERLINKED.cmd','INTERLINKED-Browser.cmd',
             'INTERLINKED-App/interlinked_gui.py','INTERLINKED-App/GUI-CHECKSUMS.json',
             'INTERLINKED-App/interlinked_controller.py','INTERLINKED-App/interlinked_hub.py','INTERLINKED-App/interlinked_mw5.py',
             'INTERLINKED-App/interlinked_master.py','INTERLINKED-App/master_hook.py','INTERLINKED-App/master_bridge.py',
             'INTERLINKED-App/interlinked_jobs.py','INTERLINKED-App/interlinked_buddy.py',
             'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
             'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config',
             'INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe.config','INTERLINKED-Desktop/Violet.png',
             'INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll','INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll',
             'INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt','INTERLINKED-Desktop/WebView2-NOTICE.txt',
             'INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
            $worker='INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe'
            $sealKey='INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json'
            $run=[guid]::NewGuid().ToString('N')
            $locks=New-Object System.Collections.ArrayList
            $receipt=[ordered]@{format='interlinked-cooler-publication-receipt-v1';status='PREFLIGHT';runId=$run;apply=[bool]$Apply;
                startedUtc=[DateTime]::UtcNow.ToString('o');files=@();verifiedPlanFiles=0;projectFilesWritten=$false;workspaceStateWritten=$false;failure=$null}
            $workspace=[IO.Path]::GetFullPath($Context.Workspace).TrimEnd('\')
            $kit=[IO.Path]::GetFullPath($Context.Kit).TrimEnd('\')
            $receiptRoot=[IO.Path]::GetFullPath($ReceiptDirectory)
            if(-not $receiptRoot.StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)-or $receiptRoot.Equals($kit,[StringComparison]::OrdinalIgnoreCase)-or $receiptRoot.StartsWith($kit+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Receipts must stay in the source workspace outside the installed kit.'}
            Assert-DesktopOrdinary $receiptRoot
            $receiptPath=Join-Path $receiptRoot ('cooler-publication-'+$run+'.json')
            function Save-CoolerReceipt {
                $temporary=$receiptPath+'.pending'
                $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($receipt|ConvertTo-Json -Depth 12))
                $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
                if(Test-Path -LiteralPath $receiptPath){[IO.File]::Replace($temporary,$receiptPath,[NullString]::Value)}else{[IO.File]::Move($temporary,$receiptPath)}
            }
            try {
                Assert-DesktopOrdinary $kit
                # A worker-only release preserves the installed GUI schema but
                # must still account for every one of its sealed companions.
                $guiSeal=Read-DesktopJson (Join-DesktopSafe $kit 'INTERLINKED-App/GUI-CHECKSUMS.json') -SingleLink
                if($guiSeal.format-cnotin @('interlinked-gui-checksums-v5','interlinked-gui-checksums-v6','interlinked-gui-checksums-v7')){throw 'Cooler publication requires an exact v5, v6 or v7 installed GUI baseline.'}
                if($guiSeal.format-cin @('interlinked-gui-checksums-v6','interlinked-gui-checksums-v7')){
                    $allowed+=@('INTERLINKED-App/interlinked_stats.py','INTERLINKED-App/interlinked_stats_process.py')
                }
                if($guiSeal.format-ceq 'interlinked-gui-checksums-v7'){$allowed+='INTERLINKED-App/interlinked_configuration.py'}
                $planCount=$allowed.Count
                $receipt['guiSealFormat']=$guiSeal.format
                $manifest=[IO.Path]::GetFullPath($ManifestPath)
                if(-not $manifest.StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication plan must stay in the source workspace.'}
                if($ManifestSha256 -notmatch '^[a-fA-F0-9]{64}$'){throw 'Invalid reviewed publication plan hash.'}
                $planBytes=Read-DesktopBytes $manifest -SingleLink
                if((Get-DesktopBytesDigest $planBytes)-cne $ManifestSha256.ToLowerInvariant()){throw 'Reviewed publication plan hash differs.'}
                $plan=[Text.Encoding]::UTF8.GetString($planBytes).TrimStart([char]0xfeff)|ConvertFrom-Json
                Assert-DesktopObjectKeys $plan @('format','files')
                if($plan.format-cne 'interlinked-desktop-publication-v1'){throw 'Unknown publication plan format.'}
                $rows=@{};$buffers=@{}
                foreach($row in $plan.files){
                    Assert-DesktopObjectKeys $row @('path','source','sha256','beforeSha256')
                    if($row.path-cnotin $allowed-or $rows.ContainsKey($row.path)){throw 'Unknown or duplicate cooler plan target.'}
                    [void](Join-DesktopSafe $kit $row.path)
                    if($row.sha256-cnotmatch '^[a-f0-9]{64}$'-or $row.beforeSha256-cnotmatch '^[a-f0-9]{64}$'){throw 'Every cooler plan row requires existing before/after SHA-256 values.'}
                    if($row.path-cnotin @($worker,$sealKey)-and $row.sha256-cne $row.beforeSha256){throw 'Cooler publication cannot change GUI, launchers, artwork or dependencies.'}
                    $source=[IO.Path]::GetFullPath($row.source)
                    if(-not $source.StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication source is outside workspace.'}
                    $bytes=Read-DesktopBytes $source -SingleLink
                    if((Get-DesktopBytesDigest $bytes)-cne $row.sha256){throw ('Reviewed source differs: '+$row.path)}
                    $rows[$row.path]=$row;$buffers[$row.path]=$bytes
                }
                if($rows.Count-ne $planCount){throw 'Incomplete cooler publication plan for the installed GUI schema.'}
                if($rows[$worker].sha256-ceq $rows[$worker].beforeSha256-or $rows[$sealKey].sha256-ceq $rows[$sealKey].beforeSha256){throw 'Cooler publication requires exactly a changed worker and matching desktop seal.'}
                foreach($name in @('INTERLINKED-GUI-State/.gui-instance.lock','MAGIX-Workspace-State/.workflow.lock')){
                    $path=Join-DesktopSafe $kit $name;Assert-DesktopOrdinary $path
                    try{$handle=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)}catch{throw ('Active or unavailable session lock: '+$name)}
                    [void]$locks.Add($handle)
                }
                Assert-DesktopIdle $Context
                Assert-DesktopBaseline $Context
                foreach($row in $rows.Values){
                    $target=Join-DesktopSafe $kit $row.path
                    $before=Read-DesktopBytes $target -SingleLink
                    if((Get-DesktopBytesDigest $before)-cne $row.beforeSha256){throw ('Existing target differs: '+$row.path)}
                }
                $oldSeal=Read-DesktopJson (Join-DesktopSafe $kit $sealKey) -SingleLink
                $newSeal=[Text.Encoding]::UTF8.GetString($buffers[$sealKey]).TrimStart([char]0xfeff)|ConvertFrom-Json
                $desktopNames=@($allowed|Where-Object {$_-like 'INTERLINKED-Desktop/*'-and $_-cne $sealKey}|ForEach-Object {$_.Substring(20)})
                foreach($seal in @($oldSeal,$newSeal)){
                    Assert-DesktopObjectKeys $seal @('format','sdkVersion','sdkArchiveSha256','createdUtc','files')
                    if($seal.format-cne 'interlinked-desktop-checksums-v1'){throw 'Unknown desktop package seal.'}
                    if($seal.sdkVersion-cne '1.0.4314.35'-or $seal.sdkArchiveSha256-cne '90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4'){throw 'Desktop SDK provenance differs from the reviewed baseline.'}
                    $created=[DateTime]::MinValue
                    if(-not [DateTime]::TryParse([string]$seal.createdUtc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind,[ref]$created)){throw 'Invalid desktop seal creation timestamp.'}
                    Assert-DesktopObjectKeys $seal.files $desktopNames
                }
                foreach($name in $desktopNames){
                    $row=$rows['INTERLINKED-Desktop/'+$name]
                    if($oldSeal.files.$name-cne $row.beforeSha256-or $newSeal.files.$name-cne $row.sha256){throw ('Desktop seal differs: '+$name)}
                    if($name-cne 'INTERLINKED.Master.Worker.exe'-and $oldSeal.files.$name-cne $newSeal.files.$name){throw 'Only the worker member may change in the desktop seal.'}
                }
                $receipt.verifiedPlanFiles=$planCount;$receipt.status='PREFLIGHT_PASSED';Save-CoolerReceipt
                if(-not $Apply){return [pscustomobject]@{status=$receipt.status;receipt=$receiptPath;verifiedPlanFiles=$planCount;files=0}}
                # Worker first, then seal. A failed partial transaction remains
                # sealed unavailable with exact backups; no forced recovery.
                foreach($key in @($worker,$sealKey)){
                    Assert-DesktopIdle $Context
                    $row=$rows[$key];$target=Join-DesktopSafe $kit $key
                    if((Get-DesktopBytesDigest (Read-DesktopBytes $target -SingleLink))-cne $row.beforeSha256){throw 'Publication target changed after preflight.'}
                    $temporary=$target+'.pending-cooler-'+$run
                    $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                    try{$bytes=$buffers[$key];$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
                    if((Get-DesktopBytesDigest (Read-DesktopBytes $temporary -SingleLink))-cne $row.sha256){throw 'Staged publication bytes differ.'}
                    $backup=$target+'.before-cooler-'+$run
                    [IO.File]::Replace($temporary,$target,$backup)
                    if((Get-DesktopBytesDigest (Read-DesktopBytes $backup -SingleLink))-cne $row.beforeSha256){throw 'Prior file was not preserved exactly.'}
                    if((Get-DesktopBytesDigest (Read-DesktopBytes $target -SingleLink))-cne $row.sha256){throw 'Installed publication bytes differ.'}
                    $receipt.files+=@([ordered]@{path=$key;sha256=$row.sha256;beforeSha256=$row.beforeSha256;backup=$backup;unchanged=$false});Save-CoolerReceipt
                }
                foreach($row in $rows.Values){if((Get-DesktopBytesDigest (Read-DesktopBytes (Join-DesktopSafe $kit $row.path) -SingleLink))-cne $row.sha256){throw ('Final installed readback differs: '+$row.path)}}
                Assert-DesktopBaseline $Context
                Assert-DesktopIdle $Context
                $receipt.status='COOLER_PUBLISHED_HASH_VERIFIED';$receipt.approvedVioletUnchanged=$true;$receipt.guiAndLaunchersUnchanged=$true;Save-CoolerReceipt
                [pscustomobject]@{status=$receipt.status;receipt=$receiptPath;verifiedPlanFiles=$planCount;files=2}
            }catch{$receipt.status='PUBLICATION_STOPPED';$receipt.failure=$_.Exception.Message;throw}
            finally{foreach($handle in $locks){$handle.Dispose()};$receipt.finishedUtc=[DateTime]::UtcNow.ToString('o');Save-CoolerReceipt}
        } $Context $ManifestPath $ManifestSha256 $ReceiptDirectory ([bool]$Apply)
    }finally{$guardStream.Dispose()}
}

Export-ModuleMember -Function Invoke-CoolerPublication
