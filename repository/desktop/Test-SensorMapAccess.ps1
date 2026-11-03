# Uses only unique synthetic memory maps. No sensor, USB, task or elevation.
# Run from normal Windows PowerShell 5.1, outside packaged/app-container contexts.
#requires -PSEdition Desktop
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SensorPath)
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SensorMapAccessProbe {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern IntPtr OpenFileMapping(uint access,bool inherit,string name);
 [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
}
'@
$assembly=[Reflection.Assembly]::LoadFile([IO.Path]::GetFullPath($SensorPath))
$type=$assembly.GetType('SensorStartup')
$method=$type.GetMethod('SensorMapSecurity',[Reflection.BindingFlags]'Static,NonPublic')
if(-not $method){throw 'Reviewed map security method is absent.'}
$security=$method.Invoke($null,@())
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sddl=$security.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
if($sddl -cne ('D:P(A;;GA;;;'+$sid+')')){throw 'Map ACL is not protected generic-all for the exact user only.'}
$legacy=[IO.MemoryMappedFiles.MemoryMappedFileSecurity]::new()
$legacy.AddAccessRule([Security.AccessControl.AccessRule[IO.MemoryMappedFiles.MemoryMappedFileRights]]::new([Security.Principal.SecurityIdentifier]::new($sid),[IO.MemoryMappedFiles.MemoryMappedFileRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
$checks=@()
foreach($case in @(@('legacy',$legacy),@('fixed',$security))){
 $name='INTERLINKED-SYNTHETIC-MAP-'+[guid]::NewGuid().ToString('N')
 $map=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew($name,4096,[IO.MemoryMappedFiles.MemoryMappedFileAccess]::ReadWrite,[IO.MemoryMappedFiles.MemoryMappedFileOptions]::None,$case[1],[IO.HandleInheritability]::None)
 try {
  $handle=[SensorMapAccessProbe]::OpenFileMapping(0xF001F,$false,$name)
  $errorCode=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
  try {
   if($case[0] -eq 'legacy' -and ($handle -ne [IntPtr]::Zero -or $errorCode -ne 5)){throw ('Legacy probe unexpected: handle='+$handle+' error='+$errorCode+' ACL='+$legacy.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access))}
   if($case[0] -eq 'fixed' -and $handle -eq [IntPtr]::Zero){throw "Fixed ACL denied native vendor section rights: $errorCode"}
   $checks+=@{case=$case[0];nativeAllAccess=($handle -ne [IntPtr]::Zero);expected=$true}
  }finally{if($handle -ne [IntPtr]::Zero){[void][SensorMapAccessProbe]::CloseHandle($handle)}}
 }finally{$map.Dispose()}
}
@{status='PASS';checks=$checks;userOnlyAcl=$true;hardwareStarted=$false}|ConvertTo-Json -Depth 4
