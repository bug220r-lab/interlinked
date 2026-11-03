# Build only; no hardware, driver installation, remote access or task changes.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,
      [Parameter(Mandatory=$true)][string]$IntelModule,
      [Parameter(Mandatory=$true)][string]$IdentityReport)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use a fresh build directory.'}
foreach($p in @($output,[IO.Path]::GetFullPath($IntelModule),[IO.Path]::GetFullPath($IdentityReport))){
 if($p.StartsWith('\\')){throw 'Ordinary local build paths required.'};$walk=$p
 while($walk){if((Test-Path -LiteralPath $walk)-and (([int][IO.File]::GetAttributes($walk)-band 0x441400)-ne 0)){throw 'Linked/cloud build path refused.'};$walk=[IO.Path]::GetDirectoryName($walk)}
}
foreach($p in @('C:\Users\Jude\Documents\MSI-Project-Library','C:\Users\Jude\AppData\Local\ProjectLibrary\Cache','C:\Program Files','C:\ProgramData\INTERLINKED')){if($output.Equals($p,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($p+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Build outside installed state.'}}
$identity=Get-Content -LiteralPath $IdentityReport -Raw|ConvertFrom-Json
$observed=if($identity.utc -is [DateTime]){$identity.utc.ToUniversalTime()}else{[DateTime]::Parse([string]$identity.utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()}
$age=([DateTime]::UtcNow-$observed).TotalHours
if($age-lt 0-or $age-gt 24-or $identity.computer-cne 'MSI'-or $identity.sid-cne 'S-1-5-21-3086848077-1833151167-136462239-1001'-or @($identity.cpu).Count-ne 1-or $identity.cpu[0].ProcessorId-cne 'BFEBFBFF000A0652'-or $identity.cpu[0].Manufacturer-cne 'GenuineIntel'-or $identity.cpu[0].Name-notmatch 'i7-10750H'){throw 'Fresh reviewed MSI CPU/account evidence required.'}
$moduleHash='ed41a0d0de082f4668a9caccb9681ebbad97a8ed3452c41918dda1eb454b77a9'
if((Get-FileHash -LiteralPath $IntelModule).Hash.ToLowerInvariant()-cne $moduleHash){throw 'Tagged LibreHardwareMonitor 0.9.6 Intel module differs.'}
[void][IO.Directory]::CreateDirectory($output);$encoding=[Text.UTF8Encoding]::new($false)
$pins='internal static class MsiCpuOnlyPins { internal const string UserSid="S-1-5-21-3086848077-1833151167-136462239-1001", ModuleHash="'+$moduleHash+'"; } internal static class MsiSensorPins { internal const string UserSid=MsiCpuOnlyPins.UserSid, CpuModelSubstring="i7-10750H", CpuSensorName="CPU Package", CpuGroupSuffix=": DTS"; }'
$pinFile=Join-Path $output 'MsiCpuOnlyPins.cs';[IO.File]::WriteAllText($pinFile,$pins,$encoding)
$exe=Join-Path $output 'INTERLINKED.MSI.Sensor.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll /reference:System.Management.dll /reference:Microsoft.CSharp.dll ('/resource:'+([IO.Path]::GetFullPath($IntelModule))+',IntelMSR') ('/out:'+$exe) $pinFile (Join-Path $PSScriptRoot 'MsiSensorCommon.cs') (Join-Path $PSScriptRoot 'MsiCpuOnly.cs') (Join-Path $PSScriptRoot 'MsiCpuSupervisor.cs')
if($LASTEXITCODE-ne 0){throw 'CPU-only build failed.'}
$selfTest=& $exe --self-test;if($LASTEXITCODE-ne 0){throw 'CPU-only pure checks failed.'}
$pureResult=$selfTest|ConvertFrom-Json
if($pureResult.status-cne 'PASS'-or $pureResult.hardwareStarted-ne $false){throw 'CPU-only pure result differs.'}
$watchdogResult=& (Join-Path $PSScriptRoot 'Test-MsiCpuWatchdog.ps1') -OutputDirectory (Join-Path $output 'watchdog-fixtures')|ConvertFrom-Json
if($watchdogResult.status-cne 'PASS'){throw 'CPU-only watchdog checks failed.'}
$supervisorResult=& (Join-Path $PSScriptRoot 'Test-MsiCpuSupervisor.ps1') -OutputDirectory (Join-Path $output 'supervisor-fixtures')|ConvertFrom-Json
if($supervisorResult.status-cne 'PASS'){throw 'CPU-only supervisor checks failed.'}
function Refusal([string]$Argument,[int]$Expected) {
 # Windows PowerShell 5.1 promotes redirected native stderr to an ErrorRecord.
 # Read the owned child's streams directly so expected refusals stay test data.
 $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$exe;$start.Arguments=$Argument
 $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
 $p=[Diagnostics.Process]::Start($start)
 try{$stdout=$p.StandardOutput.ReadToEnd();$stderr=$p.StandardError.ReadToEnd();$p.WaitForExit();if($p.ExitCode-ne $Expected){throw 'Publisher argument/host refusal failed.'}}finally{$p.Dispose()}
}
Refusal '--unexpected' 2
Refusal '--probe' 3
$publisherHash=(Get-FileHash -LiteralPath $exe).Hash.ToLowerInvariant()
$descriptor=[ordered]@{format='interlinked-msi-sensor-descriptor-v1';machine='MSI';userSid=$identity.sid;publisherSha256=$publisherHash;publisherPath='C:\Program Files\INTERLINKED MSI Sensors\INTERLINKED.MSI.Sensor.exe';outputPath='C:\ProgramData\INTERLINKED\MSI-Sensors\temperatures.json';cpuSensorName='CPU Package';cpuModelSubstring='i7-10750H';backend='pawnio-intel-cpu-only-v1';moduleSha256=$moduleHash;registers=@('0x1a2','0x1b1');ecMonitoring=$false;batteryMonitoring=$false}
[IO.File]::WriteAllText((Join-Path $output 'descriptor.json'),($descriptor|ConvertTo-Json -Depth 4),$encoding)
$record=[ordered]@{format='interlinked-msi-cpu-only-build-v1';utc=[DateTime]::UtcNow.ToString('o');publisherSha256=$publisherHash;moduleSha256=$moduleHash;moduleSource='https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/v0.9.6/LibreHardwareMonitorLib/Resources/PawnIo/IntelMSR.bin';driverVersion='2.2.0';driverInstallerSha256='1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032';source=@('MsiCpuOnly.cs','MsiCpuSupervisor.cs','MsiSensorCommon.cs','Build-MsiCpuOnly.ps1','MsiCpuWatchdogFixture.cs','Test-MsiCpuWatchdog.ps1','MsiCpuSupervisorFixture.cs','Test-MsiCpuSupervisor.ps1'|ForEach-Object {@{path=$_;sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $_)).Hash.ToLowerInvariant()}});pureChecks=[int]$pureResult.checks;watchdogChecks=[int]$watchdogResult.checks;watchdogNativeChildCases=@($watchdogResult.nativeChildCases).Count;watchdogDeadlineSeconds=$watchdogResult.deadlineSeconds;watchdogExitCode=$watchdogResult.timeoutExitCode;supervisorChecks=[int]$supervisorResult.checks;supervisorParentDeathContainment=$supervisorResult.parentDeathContainment;supervisorRetries=$supervisorResult.retries;supervisorRetrySeconds=$supervisorResult.retrySeconds;wrongArgumentAndHostChecks=2;hardwareTested=$false;installed=$false}
[IO.File]::WriteAllText((Join-Path $output 'build.json'),($record|ConvertTo-Json -Depth 5),$encoding);$record|ConvertTo-Json -Depth 5
