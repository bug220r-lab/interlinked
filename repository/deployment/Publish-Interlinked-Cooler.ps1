# Cooler-only update for the already installed THIS-MACHINE/Jude desktop kit.
# This deliberately does not publish an unrelated GUI or migrate its seal.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ManifestPath,
      [Parameter(Mandatory=$true)][string]$ManifestSha256,
      [Parameter(Mandatory=$true)][string]$ReceiptDirectory,
      [switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$workspace='C:\Users\Jude\Documents\Codex\INTERLINKED'
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
if($env:COMPUTERNAME-ine 'THIS-MACHINE'-or $env:USERNAME-ine 'Jude'-or $env:USERPROFILE-ine 'C:\Users\Jude'){throw 'Run on THIS-MACHINE as Jude.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run as the normal user, without administrator mode.'}
if([IO.Path]::GetFullPath($PSScriptRoot)-ine ($workspace+'\repository\deployment')){throw 'Use the reviewed source checkout cooler publisher.'}
if(-not ([IO.Path]::GetFullPath($ManifestPath)).StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Publication plan must be in the source workspace.'}
$context=[pscustomobject]@{Workspace=$workspace;Kit=$kit;ClientFileCount=52;
 ClientSealSha256='362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc';
 Pins=@{'magix_projects.py'='0650ab24e8d6b36ff2bd139408fcb565009e7f66d2952bec5dc91742dd7ae63f';
        'magix_workspace.py'='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa';
        'MAGIX-All-Projects-Index.json'='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'}}
$engine=Join-Path $PSScriptRoot 'Interlinked-Cooler-Publication.psm1'
# Keep the entrypoint's engine ordinary and read-locked for the whole call.
# The engine independently holds the shared guard module throughout its import
# and execution, including all idle checks, locks, staging and readback.
$walk=[IO.Path]::GetFullPath($engine)
while($walk){$item=Get-Item -LiteralPath $walk -Force;if(([long]$item.Attributes-band 0x441400)-ne 0){throw 'Link/cloud publisher engine refused.'};$parent=[IO.Path]::GetDirectoryName($walk);if($parent-eq $walk){break};$walk=$parent}
if(-not('InterlinkedCoolerEngineGuard' -as [type])){
 Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;using Microsoft.Win32.SafeHandles;
public static class InterlinkedCoolerEngineGuard {
 [StructLayout(LayoutKind.Sequential)] public struct Info {public uint Attributes,CreationLow,CreationHigh,AccessLow,AccessHigh,WriteLow,WriteHigh,Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Info info);
 public static uint Links(SafeFileHandle handle){Info info;if(!GetFileInformationByHandle(handle,out info))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());return info.Links;}
}
'@
}
$engineStream=[IO.File]::Open($engine,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
 if([InterlinkedCoolerEngineGuard]::Links($engineStream.SafeFileHandle)-ne 1){throw 'Hardlinked cooler publisher engine refused.'}
 Import-Module $engine -Force
 Invoke-CoolerPublication -Context $context -ManifestPath $ManifestPath -ManifestSha256 $ManifestSha256 -ReceiptDirectory $ReceiptDirectory -Apply:$Apply
}finally{$engineStream.Dispose()}
