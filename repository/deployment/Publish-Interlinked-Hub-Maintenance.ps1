# Scoped maintenance for the installed v2 hub; one exact reviewed plan only. No setup, cache, media,
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
if([IO.Path]::GetFullPath($PSCommandPath) -ine ($workspace+'\repository\deployment\Publish-Interlinked-Hub-Maintenance.ps1')){throw 'Use the exact reviewed maintenance entry point.'}
if(-not ([IO.Path]::GetFullPath($ManifestPath)).StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication plan must be in the source workspace.'}
$reviewedPlan=$workspace+'\.handoff\mw5-acceptance\workflow-fix\workflow-fix-plan.json'
$reviewedPlanSha256='cbea9d06ce1bf83fd1ec08aebef1e2a4a5fe0985ac6f8efc7194cc9541b5b9a2'
if([IO.Path]::GetFullPath($ManifestPath) -ine $reviewedPlan -or $ManifestSha256 -cne $reviewedPlanSha256){throw 'This maintenance accepts only its exact prepared MW5 correction plan and SHA-256.'}

$allowed=@('INTERLINKED.cmd','INTERLINKED-Browser.cmd','INTERLINKED-App/interlinked_gui.py','INTERLINKED-App/GUI-CHECKSUMS.json',
 'INTERLINKED-App/interlinked_controller.py','INTERLINKED-App/interlinked_hub.py','INTERLINKED-App/interlinked_mw5.py',
 'INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css',
 'INTERLINKED-Desktop/INTERLINKED.exe','INTERLINKED-Desktop/INTERLINKED.exe.config',
 'INTERLINKED-Desktop/Microsoft.Web.WebView2.Core.dll','INTERLINKED-Desktop/Microsoft.Web.WebView2.WinForms.dll',
 'INTERLINKED-Desktop/WebView2Loader.dll','INTERLINKED-Desktop/WebView2-LICENSE.txt',
 'INTERLINKED-Desktop/WebView2-NOTICE.txt','INTERLINKED-Desktop/DESKTOP-CHECKSUMS.json')
$context=[pscustomobject]@{Workspace=$workspace;Kit=$kit;Allowed=$allowed;ClientFileCount=52;
 ClientSealSha256='362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc';
 Pins=@{'magix_projects.py'='6c2ea58d9ac89a20c4b2bb42347b79f5802b049b2c81812314e09412578de1bf';
        'magix_workspace.py'='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa';
        'MAGIX-All-Projects-Index.json'='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'}}
$modulePath=$workspace+'\.handoff\mw5-acceptance\maintenance-publisher\Interlinked-Desktop-Publication.psm1'
$reviewedModuleSha256='14f76232c4a1d56646e8c21aac938b5c4abb024a323f273599945f6ac32d6790'
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
    $moduleSha=[Security.Cryptography.SHA256]::Create()
    try {$actualModuleSha=([BitConverter]::ToString($moduleSha.ComputeHash($moduleStream))).Replace('-','').ToLowerInvariant()}finally{$moduleSha.Dispose()}
    if($actualModuleSha -cne $reviewedModuleSha256){throw 'The reviewed maintenance module differs.'}
    $module=Import-Module $modulePath -Force -PassThru

} finally {$moduleStream.Dispose()}
# Read the exact pinned plan through the same ordinary/single-link guards.
$planBytes=& $module {param($Path) Read-DesktopBytes $Path -SingleLink} $ManifestPath
$planSha=& $module {param($Bytes) Get-DesktopBytesDigest $Bytes} $planBytes
if($planSha -cne $reviewedPlanSha256){throw 'The prepared maintenance plan bytes differ.'}
$maintenancePlan=[Text.Encoding]::UTF8.GetString($planBytes).TrimStart([char]0xfeff)|ConvertFrom-Json
$changed=@($maintenancePlan.files|Where-Object {$_.sha256 -cne $_.beforeSha256}|ForEach-Object {$_.path}|Sort-Object)
$expectedChanged=@('INTERLINKED-App/GUI-CHECKSUMS.json','INTERLINKED-App/interlinked_hub.py','INTERLINKED-App/interlinked_mw5.py')
if(@($maintenancePlan.files).Count -ne 18 -or ($changed -join '|') -cne (($expectedChanged|Sort-Object)-join '|')){throw 'Maintenance may change only the reviewed hub, MW5 adapter and their seal; all other app bytes must remain exact.'}
Invoke-DesktopPublication -Context $context -ManifestPath $ManifestPath -ManifestSha256 $ManifestSha256 -ReceiptDirectory $ReceiptDirectory -Apply:$Apply
