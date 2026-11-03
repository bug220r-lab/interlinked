# Isolated native fixtures only. No MSI connection, driver, task or app controls.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $taskOutput){throw 'Use a fresh watchdog fixture directory.'}
if($taskOutput.StartsWith('\\')){throw 'Local watchdog fixture directory required.'}
$walk=$taskOutput
while($walk){if((Test-Path -LiteralPath $walk)-and (([int][IO.File]::GetAttributes($walk)-band 0x441400)-ne 0)){throw 'Linked/cloud fixture path refused.'};$walk=[IO.Path]::GetDirectoryName($walk)}
foreach($p in @('C:\Users\Jude\Documents\MSI-Project-Library','C:\Users\Jude\AppData\Local\ProjectLibrary\Cache','C:\Program Files','C:\ProgramData\INTERLINKED')){if($taskOutput.Equals($p,[StringComparison]::OrdinalIgnoreCase)-or $taskOutput.StartsWith($p+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Fixtures must be outside installed state.'}}
[void][IO.Directory]::CreateDirectory($taskOutput)
$pins='internal static class MsiCpuOnlyPins { internal const string UserSid="S-1-5-21-3086848077-1833151167-136462239-1001", ModuleHash="ed41a0d0de082f4668a9caccb9681ebbad97a8ed3452c41918dda1eb454b77a9"; } internal static class MsiSensorPins { internal const string UserSid=MsiCpuOnlyPins.UserSid, CpuModelSubstring="i7-10750H", CpuSensorName="CPU Package", CpuGroupSuffix=": DTS"; }'
$pinPath=Join-Path $taskOutput 'MsiCpuOnlyPins.cs';[IO.File]::WriteAllText($pinPath,$pins,[Text.UTF8Encoding]::new($false))
$fixture=Join-Path $taskOutput 'MsiCpuWatchdogFixture.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /platform:x64 /optimize+ /main:MsiCpuWatchdogFixture /reference:System.Web.Extensions.dll /reference:System.Management.dll /reference:Microsoft.CSharp.dll ('/out:'+$fixture) $pinPath (Join-Path $PSScriptRoot 'MsiSensorCommon.cs') (Join-Path $PSScriptRoot 'MsiCpuOnly.cs') (Join-Path $PSScriptRoot 'MsiCpuSupervisor.cs') (Join-Path $PSScriptRoot 'MsiCpuWatchdogFixture.cs')
if($LASTEXITCODE-ne 0){throw 'CPU watchdog fixture compilation failed.'}
function RunFixture([string]$Argument,[int]$ExpectedExit) {
 $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$fixture;$start.Arguments=$Argument
 $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
 $watch=[Diagnostics.Stopwatch]::StartNew();$owned=[Diagnostics.Process]::Start($start)
 try {
  if(-not $owned.WaitForExit(5000)){$owned.Kill();[void]$owned.WaitForExit(1000);throw 'Owned watchdog fixture exceeded its exit bound.'}
  $stdout=$owned.StandardOutput.ReadToEnd();$stderr=$owned.StandardError.ReadToEnd()
  if($owned.ExitCode-ne $ExpectedExit-or $stderr){throw ('Watchdog fixture failed: '+$Argument+'; exit '+$owned.ExitCode+'; '+$stderr)}
  [pscustomobject]@{argument=$Argument;exitCode=$owned.ExitCode;seconds=$watch.Elapsed.TotalSeconds;stdout=$stdout}
 }finally{$owned.Dispose()}
}
$pure=RunFixture '' 0;$pureResult=$pure.stdout|ConvertFrom-Json
if($pureResult.status-cne 'PASS'-or $pureResult.hardwareStarted-ne $false){throw 'CPU watchdog pure result differs.'}
$children=@(RunFixture '--terminate-initialization' 4;RunFixture '--terminate-body' 4)
foreach($child in $children){if($child.stdout){throw 'Timeout termination wrote unexpected output.'}}
$record=[ordered]@{format='interlinked-msi-cpu-watchdog-fixtures-v1';status='PASS';utc=[DateTime]::UtcNow.ToString('o');checks=[int]$pureResult.checks;nativeChildCases=$children;deadlineSeconds=30;timeoutExitCode=4;fixtureSha256=(Get-FileHash -LiteralPath $fixture).Hash.ToLowerInvariant();hardwareStarted=$false;remoteAccess=$false;installed=$false}
[IO.File]::WriteAllText((Join-Path $taskOutput 'test-result.json'),($record|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false));$record|ConvertTo-Json -Depth 5
