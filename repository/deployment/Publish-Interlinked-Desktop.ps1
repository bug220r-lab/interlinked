# Guarded update for the existing THIS-MACHINE client. No setup, cache, media,
# ownership, journal or workspace restoration is performed by this publisher.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ManifestPath,
      [Parameter(Mandatory=$true)][string]$ManifestSha256,
      [Parameter(Mandatory=$true)][string]$ReceiptDirectory,
      [switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$workspace='C:\Users\Jude\Documents\Codex\INTERLINKED'
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
if($env:COMPUTERNAME -ine 'THIS-MACHINE' -or $env:USERNAME -ine 'Jude' -or $env:USERPROFILE -ine 'C:\Users\Jude'){throw 'Run on THIS-MACHINE as Jude.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run as the normal user, without administrator mode.'}
if([IO.Path]::GetFullPath($PSScriptRoot) -ine ($workspace+'\repository\deployment')){throw 'Use the reviewed source checkout publisher.'}
if(-not ([IO.Path]::GetFullPath($ManifestPath)).StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication plan must be in the source workspace.'}
$allowed=@('INTERLINKED.cmd','INTERLINKED-Browser.cmd','INTERLINKED-App/interlinked_gui.py','INTERLINKED-App/GUI-CHECKSUMS.json',
 'INTERLINKED-App/interlinked_controller.py','INTERLINKED-App/interlinked_hub.py','INTERLINKED-App/interlinked_mw5.py',
 'INTERLINKED-App/interlinked_master.py','INTERLINKED-App/master_hook.py','INTERLINKED-App/master_bridge.py',
 'INTERLINKED-App/interlinked_jobs.py','INTERLINKED-App/interlinked_buddy.py',
 'INTERLINKED-App/interlinked_stats.py','INTERLINKED-App/interlinked_stats_process.py',
 'INTERLINKED-App/interlinked_configuration.py',
 'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
 'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config',
 'INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe','INTERLINKED-Desktop/INTERLINKED.Master.Worker.exe.config','INTERLINKED-Desktop/Violet.png',
 'INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll','INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll',
 'INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt',
 'INTERLINKED-Desktop/WebView2-NOTICE.txt','INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
$context=[pscustomobject]@{Workspace=$workspace;Kit=$kit;Allowed=$allowed;ClientFileCount=52;
 ClientSealSha256='362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc';
 Pins=@{'magix_projects.py'='29434d26caa8c656ea6fd181d2a0d79e3eaad2a56b77e6b8bfae5751e22205b9';
        'magix_workspace.py'='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa';
        'MAGIX-All-Projects-Index.json'='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'}}
$modulePath=Join-Path $PSScriptRoot 'Interlinked-Desktop-Publication.psm1'
$walk=[IO.Path]::GetFullPath($modulePath)
while($walk){
    $item=Get-Item -LiteralPath $walk -Force
    if(([long]$item.Attributes -band 0x441400) -ne 0){throw 'The publication module and its ancestors must be ordinary local paths.'}
    $parent=[IO.Path]::GetDirectoryName($walk);if($parent -eq $walk){break};$walk=$parent
}
if(-not ('InterlinkedPublicationModuleGuard' -as [type])){
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class InterlinkedPublicationModuleGuard {
  [StructLayout(LayoutKind.Sequential)] public struct Info {
    public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh,
      WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
  }
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
  public static uint Links(SafeFileHandle handle) {
    Info info;
    if(!GetFileInformationByHandle(handle,out info))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    return info.Links;
  }
}
'@
}
# Keep the module read-locked while PowerShell imports its reviewed code.
$moduleStream=[IO.File]::Open($modulePath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    if([InterlinkedPublicationModuleGuard]::Links($moduleStream.SafeFileHandle) -ne 1){throw 'Hardlinked publication module refused.'}
    Import-Module $modulePath -Force
} finally {$moduleStream.Dispose()}
Invoke-DesktopPublication -Context $context -ManifestPath $ManifestPath -ManifestSha256 $ManifestSha256 -ReceiptDirectory $ReceiptDirectory -Apply:$Apply
