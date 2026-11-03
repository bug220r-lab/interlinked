# Isolated contained-child fixtures; never accesses MSI or production sensors.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $taskOutput){throw 'Use a fresh supervisor fixture directory.'}
if($taskOutput.StartsWith('\\')){throw 'Local supervisor fixture directory required.'}
$walk=$taskOutput
while($walk){if((Test-Path -LiteralPath $walk)-and (([int][IO.File]::GetAttributes($walk)-band 0x441400)-ne 0)){throw 'Linked/cloud fixture path refused.'};$walk=[IO.Path]::GetDirectoryName($walk)}
foreach($p in @('C:\Users\Jude\Documents\MSI-Project-Library','C:\Users\Jude\AppData\Local\ProjectLibrary\Cache','C:\Program Files','C:\ProgramData\INTERLINKED')){if($taskOutput.Equals($p,[StringComparison]::OrdinalIgnoreCase)-or $taskOutput.StartsWith($p+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Fixtures must be outside installed state.'}}
[void][IO.Directory]::CreateDirectory($taskOutput)
$pins='internal static class MsiCpuOnlyPins { internal const string UserSid="S-1-5-21-3086848077-1833151167-136462239-1001", ModuleHash="ed41a0d0de082f4668a9caccb9681ebbad97a8ed3452c41918dda1eb454b77a9"; } internal static class MsiSensorPins { internal const string UserSid=MsiCpuOnlyPins.UserSid, CpuModelSubstring="i7-10750H", CpuSensorName="CPU Package", CpuGroupSuffix=": DTS"; }'
$pinPath=Join-Path $taskOutput 'MsiCpuOnlyPins.cs';[IO.File]::WriteAllText($pinPath,$pins,[Text.UTF8Encoding]::new($false))
$fixture=Join-Path $taskOutput 'MsiCpuSupervisorFixture.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /platform:x64 /optimize+ /main:MsiCpuSupervisorFixture /reference:System.Web.Extensions.dll /reference:System.Management.dll /reference:Microsoft.CSharp.dll ('/out:'+$fixture) $pinPath (Join-Path $PSScriptRoot 'MsiSensorCommon.cs') (Join-Path $PSScriptRoot 'MsiCpuOnly.cs') (Join-Path $PSScriptRoot 'MsiCpuSupervisor.cs') (Join-Path $PSScriptRoot 'MsiCpuSupervisorFixture.cs')
if($LASTEXITCODE-ne 0){throw 'CPU supervisor fixture compilation failed.'}
function StartFixture([string]$arguments) {
 $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$fixture;$start.Arguments=$arguments;$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
 [Diagnostics.Process]::Start($start)
}
$owned=StartFixture ''
try {
 if(-not $owned.WaitForExit(45000)){$owned.Kill();[void]$owned.WaitForExit(1000);throw 'Owned supervisor fixture exceeded its bound.'}
 $stdout=$owned.StandardOutput.ReadToEnd();$stderr=$owned.StandardError.ReadToEnd();if($owned.ExitCode-ne 0-or $stderr){throw ('Supervisor fixture failed: '+$stderr)}
 $pure=$stdout|ConvertFrom-Json;if($pure.status-cne 'PASS'-or $pure.hardwareStarted-ne $false){throw 'Supervisor fixture result differs.'}
}finally{$owned.Dispose()}
$containmentCases=@()
$fixtureHash=(Get-FileHash -LiteralPath $fixture).Hash.ToLowerInvariant();$foreign=StartFixture ('--fixture-worker initialization-hang INTERLINKED-MSI-CPU-'+('a'*32)+' '+('a'*64)+' 1 2 '+$fixtureHash)
try {
 foreach($mode in @('suspended','initialization-hang','publication-hang')){
  $receipt=Join-Path $taskOutput ('owned-containment-'+$mode+'.txt');$parent=StartFixture ('--contain-parent '+$mode+' "'+$receipt+'"');$child=$null
  try {
   $watch=[Diagnostics.Stopwatch]::StartNew()
   while(-not (Test-Path -LiteralPath $receipt)){if($parent.HasExited-or $watch.ElapsedMilliseconds-gt 5000){throw 'Containment fixture did not start its child.'};Start-Sleep -Milliseconds 20}
   $identity=(Get-Content -LiteralPath $receipt -Raw).Trim().Split(' ');$child=[Diagnostics.Process]::GetProcessById([int]$identity[0]);$retained=$child.Handle
   if($child.StartTime.ToUniversalTime().Ticks-ne [long]$identity[1]){throw 'Owned containment child identity differs.'}
   if($mode-ceq 'publication-hang'){
    $published=Join-Path $taskOutput ('published-'+$child.Id+'.txt')
    while(-not (Test-Path -LiteralPath $published)){if($parent.HasExited-or $watch.ElapsedMilliseconds-gt 8000){throw 'Containment child did not reach blocked publication phase.'};Start-Sleep -Milliseconds 20}
   }
   $parent.Kill();if(-not $parent.WaitForExit(1500)-or -not $child.WaitForExit(2000)){throw 'Parent death did not contain its owned child.'}
   if($foreign.HasExited){throw 'Unrelated fixture process was not preserved.'}
   $containmentCases+=,[pscustomobject]@{phase=$mode;childExited=$true;unrelatedPreserved=$true}
  }finally{if(-not $parent.HasExited){$parent.Kill();[void]$parent.WaitForExit(1500)};if($null-ne $child){$child.Dispose()};$parent.Dispose()}
 }
}finally{if(-not $foreign.HasExited){$foreign.Kill();[void]$foreign.WaitForExit(1500)};$foreign.Dispose()}
$record=[ordered]@{format='interlinked-msi-cpu-supervisor-fixtures-v1';status='PASS';utc=[DateTime]::UtcNow.ToString('o');checks=[int]$pure.checks;parentDeathContainment=($containmentCases.Count-eq 3);containmentCases=$containmentCases;deadlineSeconds=30;retrySeconds=60;retries=3;fixtureSha256=$fixtureHash;hardwareStarted=$false;driverStarted=$false;remoteAccess=$false;installed=$false}
[IO.File]::WriteAllText((Join-Path $taskOutput 'test-result.json'),($record|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false));$record|ConvertTo-Json -Depth 4
