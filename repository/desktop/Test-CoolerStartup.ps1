# Native refusal/resource checks only. Never starts the privileged setup or sensors.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildDirectory)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run these refusal fixtures unelevated.'}
$build=[IO.Path]::GetFullPath($BuildDirectory)
$record=Get-Content -LiteralPath (Join-Path $build 'build.json') -Raw|ConvertFrom-Json
$sensor=Join-Path $build 'INTERLINKED.Sensor.Startup.exe'
$setup=Join-Path $build 'INTERLINKED.Cooler.Startup.Setup.exe'
$checks=[Collections.Generic.List[string]]::new()
foreach($pair in @(@($sensor,$record.sensorSha256),@($setup,$record.setupSha256))){if((Get-FileHash -LiteralPath $pair[0]).Hash.ToLowerInvariant() -cne $pair[1]){throw 'Built bytes changed.'}}
$checks.Add('Both executable hashes match the frozen build')
foreach($probe in @(@('--fixture-invalid',2),@('',3))){
    $start=[Diagnostics.ProcessStartInfo]::new($sensor,[string]$probe[0]);$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden'
    $process=[Diagnostics.Process]::Start($start)
    try{if(-not $process.WaitForExit(5000)){throw 'Refusal fixture timed out.'};if($process.ExitCode -ne $probe[1]){throw 'Sensor host accepted an invalid launch.'}}finally{$process.Dispose()}
    $checks.Add('Sensor launch refused with expected exit '+$probe[1])
}
$assembly=[Reflection.Assembly]::LoadFile($setup)
$payload=$assembly.GetManifestResourceStream('sensor')
try{$sha=[Security.Cryptography.SHA256]::Create();try{$embedded=[BitConverter]::ToString($sha.ComputeHash($payload)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}finally{$payload.Dispose()}
if($embedded -cne $record.sensorSha256){throw 'Embedded sensor payload differs.'}
$checks.Add('Setup embeds exactly the reviewed fixed-function publisher')
$setupType=$assembly.GetType('CoolerStartupSetup')
$missing=$setupType.GetMethod('TaskMissing',[Reflection.BindingFlags]'NonPublic,Static')
foreach($case in @(@([IO.FileNotFoundException]::new(),$true),@([Runtime.InteropServices.COMException]::new('missing',-2147024894),$true),@([UnauthorizedAccessException]::new(),$false))){
 if($missing.Invoke($null,@($case[0])) -ne $case[1]){throw 'First-install task exception classification differs.'}
}
$checks.Add('Missing-task FileNotFound and COM variants are accepted while access denied is preserved')
$principal=$setupType.GetMethod('PrincipalMatches',[Reflection.BindingFlags]'NonPublic,Static')
if(-not $principal.Invoke($null,@($record.userSid)) -or -not $principal.Invoke($null,@('Jude')) -or $principal.Invoke($null,@('S-1-5-18'))){throw 'Task principal normalization differs.'}
$checks.Add('Reviewed account names and SIDs normalize without granting another account')
$sensorAssembly=[Reflection.Assembly]::LoadFile($sensor)
foreach($name in @('UserSid','ExeHash','DllHash')){
 if($sensorAssembly.GetType('SensorStartup').GetField($name,[Reflection.BindingFlags]'NonPublic,Static').GetRawConstantValue() -cne $setupType.GetField($name,[Reflection.BindingFlags]'NonPublic,Static').GetRawConstantValue()){throw 'Sensor payload account or vendor pins differ.'}
}
$checks.Add('Reused or compiled sensor payload retains the exact reviewed account and vendor pins')
if($setupType.GetField('PreviousSetupHash',[Reflection.BindingFlags]'NonPublic,Static').GetRawConstantValue() -cne $record.previousSetupSha256){throw 'The repair build does not bind its reviewed predecessor hash.'}
$checks.Add('Repair capability binds exactly the reviewed prior setup hash')
$stateType=$assembly.GetType('CoolerStartupSetup+TaskState')
$taskValidator=$setupType.GetMethod('ValidateTaskState',[Reflection.BindingFlags]'NonPublic,Static')
$folderValidator=$setupType.GetMethod('ValidateFolderSecurity',[Reflection.BindingFlags]'NonPublic,Static')
$taskSecurityValidator=$setupType.GetMethod('ValidateTaskSecurity',[Reflection.BindingFlags]'NonPublic,Static')
$valid=[ordered]@{
 ActionCount=1;ActionType=0;ActionPath='C:\Program Files\INTERLINKED Cooler Startup\INTERLINKED.Sensor.Startup.exe';Arguments='';WorkingDirectory='C:\Program Files\INTERLINKED Cooler Startup';
 PrincipalSid=$record.userSid;LogonType=3;RunLevel=1;TriggerCount=1;TriggerType=9;TriggerEnabled=$true;TriggerSid=$record.userSid;
 MultipleInstances=2;RestartCount=3;RestartInterval='PT1M';ExecutionTimeLimit='PT0S';DisallowBatteries=$false;StopOnBatteries=$false
}
function TaskState($Values){
 $state=[Activator]::CreateInstance($stateType,$true)
 foreach($field in $Values.Keys){$member=$stateType.GetField($field,[Reflection.BindingFlags]'NonPublic,Instance');$member.SetValue($state,[Convert]::ChangeType($Values[$field],$member.FieldType))}
 return $state
}
[void]$taskValidator.Invoke($null,@((TaskState $valid)))
$checks.Add('Exact fixed logon task definition passes pure ownership validation')
$named=[ordered]@{};foreach($name in $valid.Keys){$named[$name]=$valid[$name]};$named.PrincipalSid='Jude';$named.TriggerSid='Jude'
[void]$taskValidator.Invoke($null,@((TaskState $named)))
$checks.Add('Full task validation accepts equivalent reviewed account-name readback')
$invalid=[ordered]@{ActionCount=2;ActionType=5;ActionPath='C:\fixture\foreign.exe';Arguments='--foreign';WorkingDirectory='C:\fixture';PrincipalSid='S-1-5-18';LogonType=1;RunLevel=0;TriggerCount=2;TriggerType=1;TriggerEnabled=$false;TriggerSid='S-1-5-18';MultipleInstances=0;RestartCount=10;RestartInterval='PT1S';ExecutionTimeLimit='PT1H';DisallowBatteries=$true;StopOnBatteries=$true}
foreach($field in $invalid.Keys){
 $changed=[ordered]@{};foreach($name in $valid.Keys){$changed[$name]=$valid[$name]};$changed[$field]=$invalid[$field]
 $changedState=TaskState $changed
 $refused=$false;try{[void]$taskValidator.Invoke($null,@($changedState))}catch{$refused=$true}
 if(-not $refused){throw ('Foreign task field accepted: '+$field)}
 $checks.Add('Task ownership refuses changed '+$field)
}
function FolderSecurity([string]$Owner,[bool]$Protected,[bool]$UserWrite){
 $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new($Owner));$acl.SetAccessRuleProtection($Protected,$false)
 foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))}
 $rights=if($UserWrite){[Security.AccessControl.FileSystemRights]::Modify}else{[Security.AccessControl.FileSystemRights]::ReadAndExecute}
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),$rights,[Security.AccessControl.AccessControlType]::Allow))
 return $acl
}
[void]$folderValidator.Invoke($null,@((FolderSecurity 'S-1-5-32-544' $true $false)))
$checks.Add('Protected administrator-owned folder permits read-only normal users')
foreach($fixture in @(@('S-1-5-32-545',$true,$false),@('S-1-5-32-544',$false,$false),@('S-1-5-32-544',$true,$true))){
 $changedSecurity=FolderSecurity $fixture[0] $fixture[1] $fixture[2]
 $refused=$false;try{[void]$folderValidator.Invoke($null,@($changedSecurity))}catch{$refused=$true}
 if(-not $refused){throw 'Unsafe startup folder permissions accepted.'}
 $checks.Add('Unsafe owner, inherited access or normal-user writes are refused')
}
$taskSddl='O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;'+$record.userSid+')'
[void]$taskSecurityValidator.Invoke($null,@($taskSddl))
$checks.Add('Task DACL grants only administrators and SYSTEM write access')
foreach($changedSddl in @($taskSddl.Replace('O:BA','O:'+$record.userSid),$taskSddl.Replace('D:P','D:'),$taskSddl.Replace('GRGX','GA'),$taskSddl.Replace(';;;'+$record.userSid,';;;S-1-5-32-545'))){
 $refused=$false;try{[void]$taskSecurityValidator.Invoke($null,@($changedSddl))}catch{$refused=$true}
 if(-not $refused){throw 'Unsafe startup task permissions accepted.'}
 $checks.Add('Unsafe task ownership, inherited access, user writes or foreign user is refused')
}
$builder=Join-Path $PSScriptRoot 'Build-CoolerStartup.ps1'
foreach($output in @('C:\Users\Jude\Documents\MSI-Project-Library','C:\Users\Jude\AppData\Local\ProjectLibrary\Cache','\\fixture\share\startup-build',$build)){
 $refused=$false;try{& $builder -OutputDirectory $output -UserSid $record.userSid -ReviewedAppPath $record.reviewedAppPath|Out-Null}catch{$refused=$true}
 if(-not $refused){throw 'Unsafe or existing build output was accepted.'}
 $checks.Add('Build refuses production, cache, UNC or existing output before creating files')
}
$repairType=$assembly.GetType('CoolerStartupSetup+RepairState');$repairValidator=$setupType.GetMethod('ValidateRepairState',[Reflection.BindingFlags]'NonPublic,Static')
$repair=[ordered]@{TaskExists=$true;FolderExists=$true;LiveOptInExists=$false;FolderProtected=$true;SensorProtected=$true;SetupProtected=$true;SensorDigest=('1'*64);SetupDigest=('2'*64);ExpectedSensorDigest=('1'*64);ExpectedSetupDigest=('2'*64);BeforeTrccDigest=$null;ReviewedTrccDigest=('3'*64)}
function RepairState($Values){$state=[Activator]::CreateInstance($repairType,$true);foreach($name in $Values.Keys){$repairType.GetField($name,[Reflection.BindingFlags]'NonPublic,Instance').SetValue($state,$Values[$name])};return $state}
[void]$repairValidator.Invoke($null,@((RepairState $repair)))
$checks.Add('Partial repair requires the exact prior setup and sensor with protected ownership and no live opt-in')
$invalidRepair=[ordered]@{TaskExists=$false;FolderExists=$false;LiveOptInExists=$true;FolderProtected=$false;SensorProtected=$false;SetupProtected=$false;SensorDigest=('0'*64);SetupDigest=('0'*64);ExpectedSetupDigest='';BeforeTrccDigest=('4'*64)}
foreach($name in $invalidRepair.Keys){
 $changed=[ordered]@{};foreach($key in $repair.Keys){$changed[$key]=$repair[$key]};$changed[$name]=$invalidRepair[$name];$state=RepairState $changed
 $refused=$false;try{[void]$repairValidator.Invoke($null,@($state))}catch{$refused=$true}
 if(-not $refused){throw ('Unsafe partial repair accepted: '+$name)}
 $checks.Add('Partial repair refuses changed ownership, task, committed opt-in, payload hash or prior TRCC phase')
}
$repair.BeforeTrccDigest=$repair.ReviewedTrccDigest;[void]$repairValidator.Invoke($null,@((RepairState $repair)))
$checks.Add('An identical protected prior TRCC receipt can resume before any external task change')
$copyValidator=$setupType.GetMethod('ExistingCopyMatches',[Reflection.BindingFlags]'NonPublic,Static')
foreach($fixture in @(@($true,'same','same',$true),@($false,'same','same',$false),@($true,'foreign','same',$false))){
 if($copyValidator.Invoke($null,@($fixture[0],$fixture[1],$fixture[2])) -ne $fixture[3]){throw 'Owned copy collision policy differs.'}
 $checks.Add('Only an identical protected copy can be reused; differing records remain preserved')
}
$restoreValidator=$setupType.GetMethod('TrccRestoreAllowed',[Reflection.BindingFlags]'NonPublic,Static')
$trccBefore='<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Principals><Principal id="Author"><UserId>'+$record.userSid+'</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals><Triggers><LogonTrigger /></Triggers><Settings><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec><Command>"C:\Program Files\TRCCCAP\TRCC.exe"</Command></Exec></Actions></Task>'
$trccDisabled=$trccBefore.Replace('<Enabled>true</Enabled>','<Enabled>false</Enabled>')
function TextHash([string]$Text){$sha=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
$reviewedHash=TextHash $trccBefore
if(-not $restoreValidator.Invoke($null,@($trccBefore,$trccDisabled,$reviewedHash))){throw 'Exact reviewed disabled TRCC task could not restore.'}
$checks.Add('TRCC restore accepts only the reviewed originally enabled task with Enabled as its sole change')
$trccDefaultEnabled=$trccBefore.Replace('<Enabled>true</Enabled>','')
if(-not $restoreValidator.Invoke($null,@($trccDefaultEnabled,$trccDisabled,(TextHash $trccDefaultEnabled)))){throw 'Default enabled TRCC state was not recognized.'}
$checks.Add('TRCC restore recognizes the reviewed vendor default when Enabled was omitted')
foreach($fixture in @(
 @($trccBefore,$trccDisabled,('0'*64)),
 @($trccDisabled,$trccDisabled,(TextHash $trccDisabled)),
 @($trccBefore,$trccBefore,$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('TRCC.exe','foreign.exe'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('</Command>','</Command><Arguments>foreign</Arguments>'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace($record.userSid,'S-1-5-18'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('InteractiveToken','Password'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('HighestAvailable','LeastPrivilege'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('LogonTrigger','BootTrigger'),$reviewedHash),
 @($trccBefore,$trccDisabled.Replace('<Settings>','<Settings><Hidden>true</Hidden>'),$reviewedHash),
 @($trccBefore,('<!DOCTYPE Task [<!ENTITY foreign SYSTEM "file:///C:/fixture">]>'+$trccDisabled),$reviewedHash)
)){
 if($restoreValidator.Invoke($null,@($fixture[0],$fixture[1],$fixture[2]))){throw 'Foreign, changed, originally disabled or invalid TRCC task was accepted for restoration.'}
 $checks.Add('TRCC restore refuses changed review hash, original state, action, account, trigger, settings or XML declarations')
}
$disableMethod=$setupType.GetMethod('DisableStartup',[Reflection.BindingFlags]'NonPublic,Static')
foreach($failedStep in @('','disable','stop','restore','opt-out')){
 $events=[Collections.Generic.List[string]]::new()
 $optOut=[Action]{$events.Add('opt-out');if($failedStep -eq 'opt-out'){throw 'Fixture opt-out failure'}}
 $disable=[Action]{$events.Add('disable');if($failedStep -eq 'disable'){throw 'Fixture disable failure'}}
 $stop=[Action]{$events.Add('stop');if($failedStep -eq 'stop'){throw 'Fixture stop failure'}}
 $restore=[Action]{$events.Add('restore');if($failedStep -eq 'restore'){throw 'Fixture foreign TRCC task refusal'}}
 $failed=$false;try{[void]$disableMethod.Invoke($null,@($optOut,$disable,$stop,$restore))}catch{$failed=$true}
 $expected=if($failedStep -eq 'opt-out'){'opt-out'}else{'opt-out,disable,stop,restore'}
 if(($events -join ',') -cne $expected -or $failed -ne [bool]$failedStep){throw 'Disable ordering or failure reporting differs.'}
 $checks.Add('Opt-out precedes shutdown and TRCC restoration still runs after owned-task failure')
}
$completeMethod=$setupType.GetMethod('CompleteStartup',[Reflection.BindingFlags]'NonPublic,Static')
foreach($failedRollback in @('','opt-out','disable','restore')){
 $events=[Collections.Generic.List[string]]::new()
 $install=[Action]{$events.Add('install');throw 'Fixture final opt-in write failure'}
 $optOut=[Action]{$events.Add('opt-out');if($failedRollback -eq 'opt-out'){throw 'Fixture opt-out failure'}}
 $disable=[Action]{$events.Add('disable');if($failedRollback -eq 'disable'){throw 'Fixture disable failure'}}
 $restore=[Action]{$events.Add('restore');if($failedRollback -eq 'restore'){throw 'Fixture changed TRCC task refusal'}}
 $failed=$false;try{[void]$completeMethod.Invoke($null,@($install,$optOut,$disable,$restore))}catch{$failed=$true}
 if(-not $failed -or ($events -join ',') -cne 'install,opt-out,disable,restore'){throw 'Setup failure did not complete all rollback attempts in order.'}
 $checks.Add('Setup write failure opts out first and attempts every owned-task/TRCC rollback step')
}
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;using System.Text;
public static class StartupManifestProbe {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr LoadLibraryEx(string path,IntPtr file,uint flags);
 [DllImport("kernel32.dll",SetLastError=true)]static extern IntPtr FindResource(IntPtr module,IntPtr name,IntPtr type);
 [DllImport("kernel32.dll")]static extern uint SizeofResource(IntPtr module,IntPtr resource);
 [DllImport("kernel32.dll")]static extern IntPtr LoadResource(IntPtr module,IntPtr resource);
 [DllImport("kernel32.dll")]static extern IntPtr LockResource(IntPtr resource);
 [DllImport("kernel32.dll")]static extern bool FreeLibrary(IntPtr module);
 public static string Read(string path){var module=LoadLibraryEx(path,IntPtr.Zero,2);if(module==IntPtr.Zero)throw new Exception("Resource load failed");try{var resource=FindResource(module,(IntPtr)1,(IntPtr)24);if(resource==IntPtr.Zero)throw new Exception("Manifest absent");var bytes=new byte[SizeofResource(module,resource)];Marshal.Copy(LockResource(LoadResource(module,resource)),bytes,0,bytes.Length);return Encoding.UTF8.GetString(bytes);}finally{FreeLibrary(module);}}
}
'@
[xml]$manifest=[StartupManifestProbe]::Read($setup)
$requested=$manifest.SelectSingleNode('//*[local-name()="requestedExecutionLevel"]')
if($requested.level -cne 'requireAdministrator' -or $requested.uiAccess -cne 'false'){throw 'Setup elevation manifest differs.'}
$checks.Add('Only the one-time setup requests Windows administrator approval')
$result=[ordered]@{format='interlinked-cooler-startup-tests-v1';ok=$true;checks=@($checks);count=$checks.Count;hardwareStarted=$false;taskRegistered=$false}
[IO.File]::WriteAllText((Join-Path $build 'tests.json'),($result|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
$result|ConvertTo-Json -Depth 4
