# Explicit MSI-only migration from the exact disabled vendor publisher.
# No app/node/USB changes. A failed migration restores bytes and leaves task disabled.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$StageDirectory,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$PublisherSha256,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$DescriptorSha256,
      [ValidatePattern('^[a-f0-9]{64}$')][string]$PreviousPublisherSha256='861c649b0baccf3d2d39ecd77fa9c9a1475b66951131e19012a6e50929d177bd',
      [ValidatePattern('^[a-f0-9]{64}$')][string]$PreviousDescriptorSha256='dda4ce6eeda7b6bc8d60d02f438b593336f8e2fda64169d817794fd6aafc3d33')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$who=[Security.Principal.WindowsIdentity]::GetCurrent()
if($env:COMPUTERNAME-cne 'MSI'-or $who.User.Value-cne 'S-1-5-21-3086848077-1833151167-136462239-1001'-or -not ([Security.Principal.WindowsPrincipal]::new($who)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Reviewed MSI administrator required.'}
$stage=[IO.Path]::GetFullPath($StageDirectory)
if(-not $stage.StartsWith('C:\ProgramData\INTERLINKED-MSI-Sensor-Staging\cpu-only-',[StringComparison]::OrdinalIgnoreCase)){throw 'Protected CPU-only staging directory required.'}
$folder='C:\Program Files\INTERLINKED MSI Sensors';$data='C:\ProgramData\INTERLINKED\MSI-Sensors'
$paths=@{publisher=(Join-Path $folder 'INTERLINKED.MSI.Sensor.exe');descriptor=(Join-Path $data 'descriptor.json')}
$oldHashes=@{publisher=$PreviousPublisherSha256;descriptor=$PreviousDescriptorSha256}
$newHashes=@{publisher=$PublisherSha256;descriptor=$DescriptorSha256};$names=@{publisher='INTERLINKED.MSI.Sensor.exe';descriptor='descriptor.json'}
function Hash([string]$p){(Get-FileHash -LiteralPath $p).Hash.ToLowerInvariant()}
function Definition([string]$s){$x=[Xml.XmlDocument]::new();$x.XmlResolver=$null;$x.LoadXml($s);$n=[Xml.XmlNamespaceManager]::new($x.NameTable);$n.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task');foreach($p in @('/t:Task/t:RegistrationInfo/t:SecurityDescriptor','/t:Task/t:Settings/t:Enabled')){$v=$x.SelectSingleNode($p,$n);if($null-ne $v){[void]$v.ParentNode.RemoveChild($v)}};$x.OuterXml}
function TextHash([string]$s){$h=[Security.Cryptography.SHA256]::Create();try{[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($s))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}}
$setup=Join-Path $folder 'INTERLINKED.MSI.Sensors.Setup.exe'
if((Hash $setup)-cne '9f06a5e382021f5e6b2beadac936cee610fddec73432275680788a743dae0bd7'){throw 'Retained reviewed setup differs.'}
$assembly=[Reflection.Assembly]::Load([IO.File]::ReadAllBytes($setup));$flags=[Reflection.BindingFlags]'NonPublic,Static';$common=$assembly.GetType('MsiSensorCommon');$protected=$common.GetMethod('Protected',$flags);$newFile=$common.GetMethod('NewProtectedFile',$flags)
function Protected([string]$p,[bool]$directory){[void]$protected.Invoke($null,@($p,$directory))}
function NewFile([string]$p,[byte[]]$bytes){$f=$newFile.Invoke($null,@($p));try{$f.Write($bytes,0,$bytes.Length);$f.Flush($true)}finally{$f.Dispose()}}
foreach($p in @($folder,(Join-Path $folder 'Vendor'),'C:\ProgramData\INTERLINKED',$data,(Split-Path $stage),$stage)){Protected $p $true}
$oldType=$assembly.GetType('MsiSensorSetup');$read=$oldType.GetMethod('ReadTaskState',$flags);$validate=$oldType.GetMethod('ValidateTaskState',$flags);$security=$oldType.GetMethod('ValidateTaskSecurity',$flags)
$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$task=$scheduler.GetFolder('\').GetTask('INTERLINKED MSI Sensor Telemetry')
[void]$validate.Invoke($null,@($read.Invoke($null,@($task.Definition))));[void]$security.Invoke($null,@([string]$task.GetSecurityDescriptor(7)))
if($task.Enabled-or [int]$task.State-ne 1-or (TextHash (Definition ([string]$task.Xml)))-cne '0b005dd9c7c8e1fc55e0e750d6b0575f95287399de290f0da029627736ec002e'){throw 'Exact disabled predecessor task required.'}
if(@(Get-Process HWINFO,HWiNFO64,INTERLINKED.MSI.Sensor,LibreHardwareMonitor,OpenHardwareMonitor -ErrorAction SilentlyContinue).Count){throw 'Existing sensor processes preserved.'}
foreach($pair in @(@('HWINFO.exe','c7225253635605492ea7e091aacdfe701005a0878dd4fedfed6186968efda479'),@('HWiNFO64.dll','c0ad86626b067f4e972546265dca95c9f81ec87e90830ae2cd3c399929a09bd5'))){$p=Join-Path $folder ('Vendor\'+$pair[0]);Protected $p $false;if((Hash $p)-cne $pair[1]){throw 'Retained vendor differs.'}}
$locks=@();$replaced=@();$backups=@{};$transaction=[guid]::NewGuid().ToString('N');$beforeXml=[string]$task.Xml;$beforeSddl=[string]$task.GetSecurityDescriptor(7)
try{
 foreach($key in @('publisher','descriptor')){
  Protected $paths[$key] $false;$source=Join-Path $stage $names[$key];Protected $source $false
  $locks+=,[IO.File]::Open($source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  if((Hash $source)-cne $newHashes[$key]-or (Hash $paths[$key])-cne $oldHashes[$key]){throw ('Exact predecessor/candidate differs: '+$key)}
 }
 $next=Get-Content -LiteralPath (Join-Path $stage 'descriptor.json') -Raw|ConvertFrom-Json
 if($next.publisherSha256-cne $PublisherSha256-or $next.backend-cne 'pawnio-intel-cpu-only-v1'-or $next.ecMonitoring-ne $false-or $next.batteryMonitoring-ne $false){throw 'CPU-only descriptor mismatch.'}
 NewFile (Join-Path $data ('task.before-cpu-only-'+$transaction+'.xml')) ([Text.Encoding]::Unicode.GetBytes($beforeXml))
 foreach($key in @('publisher','descriptor')){
  $pending=$paths[$key]+'.pending-'+$transaction;$backups[$key]=$paths[$key]+'.before-cpu-only-'+$transaction
  NewFile $pending ([IO.File]::ReadAllBytes((Join-Path $stage $names[$key])))
  if((Hash $pending)-cne $newHashes[$key]){throw 'Protected pending copy differs.'}
  [IO.File]::Replace($pending,$paths[$key],$backups[$key]);$replaced+=,$key;Protected $paths[$key] $false
 }
 [void]$validate.Invoke($null,@($read.Invoke($null,@($task.Definition))));[void]$security.Invoke($null,@([string]$task.GetSecurityDescriptor(7)))
 if((Definition ([string]$task.Xml))-cne (Definition $beforeXml)){throw 'Task definition changed.'}
 $task.Enabled=$true;$task.SetSecurityDescriptor('O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)',0x10)
 [void]$security.Invoke($null,@([string]$task.GetSecurityDescriptor(7)));[void]$task.Run($null)
 Start-Sleep -Seconds 5
 $telemetry=Get-Content -LiteralPath (Join-Path $data 'temperatures.json') -Raw|ConvertFrom-Json
 if([int]$task.State-ne 4-or $telemetry.publisherSha256-cne $PublisherSha256-or $telemetry.cpuTemperature.status-cne 'verified'-or $null-eq $telemetry.cpuTemperature.value-or ([DateTime]::UtcNow-[DateTime]::Parse($telemetry.cpuTemperature.observedAt).ToUniversalTime()).TotalSeconds-gt 3){throw 'Fresh production CPU temperature not observed.'}
 $rollback=@'
$ErrorActionPreference='Stop'
$who=[Security.Principal.WindowsIdentity]::GetCurrent()
if($env:COMPUTERNAME-cne 'MSI'-or $who.User.Value-cne 'S-1-5-21-3086848077-1833151167-136462239-1001'-or -not ([Security.Principal.WindowsPrincipal]::new($who)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Reviewed MSI administrator required.'}
$record=Get-Content -LiteralPath '__RECORD__' -Raw|ConvertFrom-Json
$sc=New-Object -ComObject Schedule.Service;$sc.Connect();$t=$sc.GetFolder('\').GetTask('INTERLINKED MSI Sensor Telemetry')
if($t.Definition.Actions.Count-ne 1-or $t.Definition.Actions.Item(1).Path-cne 'C:\Program Files\INTERLINKED MSI Sensors\INTERLINKED.MSI.Sensor.exe'-or $t.Definition.Actions.Item(1).Arguments){throw 'Task action differs.'}
$folder='C:\Program Files\INTERLINKED MSI Sensors';$data='C:\ProgramData\INTERLINKED\MSI-Sensors'
$paths=@{publisher=(Join-Path $folder 'INTERLINKED.MSI.Sensor.exe');descriptor=(Join-Path $data 'descriptor.json')}
foreach($k in @('publisher','descriptor')){if((Get-FileHash -LiteralPath $paths[$k]).Hash.ToLowerInvariant()-cne $record.after.$k-or (Get-FileHash -LiteralPath $record.backups.$k).Hash.ToLowerInvariant()-cne $record.before.$k){throw 'Exact rollback hashes differ.'}}
$t.Enabled=$false;if([int]$t.State-in @(2,4)){$t.Stop(0)}
Start-Sleep -Seconds 2
if(@(Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath-ieq $paths.publisher}).Count){throw 'Owned publisher has not exited; bytes preserved.'}
foreach($k in @('descriptor','publisher')){$pending=$paths[$k]+'.rollback-'+[guid]::NewGuid().ToString('N');[IO.File]::Copy($record.backups.$k,$pending,$false);Set-Acl -LiteralPath $pending -AclObject (Get-Acl -LiteralPath $record.backups.$k);[IO.File]::Replace($pending,$paths[$k],$paths[$k]+'.retired-cpu-only-'+[guid]::NewGuid().ToString('N'))}
'RESTORED_VENDOR_BYTES_TASK_REMAINS_DISABLED; PawnIO retained as separate dependency'
'@
 $recordPath=Join-Path $data ('cpu-only-publication-'+$transaction+'.json');$rollbackPath=Join-Path $folder ('Rollback-CpuOnly-'+$transaction+'.ps1')
 $record=[ordered]@{format='interlinked-msi-cpu-only-publication-v1';status='PUBLISHED';utc=[DateTime]::UtcNow.ToString('o');transaction=$transaction;before=$oldHashes;after=$newHashes;backups=$backups;rollbackScript=$rollbackPath;beforeTaskSddl=$beforeSddl;afterTaskSddl=[string]$task.GetSecurityDescriptor(7);taskDefinitionPreserved=((Definition ([string]$task.Xml))-ceq (Definition $beforeXml));taskEnabled=$task.Enabled;taskState=$task.State;telemetry=$telemetry;vendorBytesRetained=$true;vendorHelperStarted=$false;nodeOrAppChanged=$false;installedPayloadAclVerified=$true}
 NewFile $recordPath ([Text.UTF8Encoding]::new($false).GetBytes(($record|ConvertTo-Json -Depth 7)))
 NewFile $rollbackPath ([Text.UTF8Encoding]::new($false).GetBytes($rollback.Replace('__RECORD__',$recordPath)))
 $record|ConvertTo-Json -Depth 7
}catch{
 $failure=$_;$task.Enabled=$false;if([int]$task.State-in @(2,4)){$task.Stop(0)};Start-Sleep -Seconds 2
 if(-not @(Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath-ieq $paths.publisher}).Count){
  for($index=$replaced.Count-1;$index-ge 0;$index--){$key=$replaced[$index];$pending=$paths[$key]+'.rollback-'+$transaction;NewFile $pending ([IO.File]::ReadAllBytes($backups[$key]));[IO.File]::Replace($pending,$paths[$key],$paths[$key]+'.failed-cpu-only-'+$transaction)}
 }
 throw $failure
}finally{foreach($f in $locks){$f.Dispose()}}
