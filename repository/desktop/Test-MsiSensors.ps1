# Isolated refusal/pure fixtures only; never starts setup, tasks or sensor drivers.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildDirectory)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$build=[IO.Path]::GetFullPath($BuildDirectory)
$record=Get-Content -LiteralPath (Join-Path $build 'build.json') -Raw|ConvertFrom-Json
foreach($pair in @(@('INTERLINKED.MSI.Sensor.exe',$record.publisherSha256),@('INTERLINKED.MSI.Sensors.Setup.exe',$record.setupSha256),@('HWINFO.exe',$record.vendorHashes.'HWINFO.exe'),@('HWiNFO64.dll',$record.vendorHashes.'HWiNFO64.dll'))){if((Get-FileHash -LiteralPath (Join-Path $build $pair[0])).Hash.ToLowerInvariant() -cne $pair[1]){throw 'MSI candidate bytes changed.'}}
$setup=[Reflection.Assembly]::LoadFile((Join-Path $build 'INTERLINKED.MSI.Sensors.Setup.exe'))
foreach($pair in @(@('publisher',$record.publisherSha256),@('vendor-exe',$record.vendorHashes.'HWINFO.exe'),@('vendor-dll',$record.vendorHashes.'HWiNFO64.dll'))){
 $stream=$setup.GetManifestResourceStream($pair[0]);try{$sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}finally{$stream.Dispose()};if($hash -cne $pair[1]){throw 'MSI embedded payload mismatch.'}
}
$fixture=Join-Path $build 'MsiSensorFixture.exe';$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /main:MsiSensorFixture /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll /reference:Microsoft.CSharp.dll ('/out:'+$fixture) (Join-Path $build 'MsiSensorPins.cs') (Join-Path $PSScriptRoot 'MsiSensorCommon.cs') (Join-Path $PSScriptRoot 'MsiSensorSetup.cs') (Join-Path $PSScriptRoot 'MsiSensorFixture.cs')
if($LASTEXITCODE -ne 0){throw 'MSI sensor fixture compilation failed.'}
$result=& $fixture
if($LASTEXITCODE -ne 0){throw 'MSI sensor pure fixture failed.'}
$result|Set-Content -LiteralPath (Join-Path $build 'test-result.json') -Encoding UTF8
# The publisher has no auto-elevation manifest. On THIS-MACHINE it must refuse.
if($env:COMPUTERNAME -ceq 'THIS-MACHINE'){
 foreach($case in @(@('--invalid',2),@('',3))){$start=[Diagnostics.ProcessStartInfo]::new((Join-Path $build 'INTERLINKED.MSI.Sensor.exe'),$case[0]);$start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WindowStyle='Hidden';$process=[Diagnostics.Process]::Start($start);try{if(-not $process.WaitForExit(5000)-or $process.ExitCode -ne $case[1]){throw 'MSI publisher accepted invalid host/arguments.'}}finally{$process.Dispose()}}
 # Invoke only the wrong-host guard in-process; this does not launch the
 # administrator-manifest executable or request UAC. CLI refusal must not show UI.
 $main=$setup.GetType('MsiSensorSetup').GetMethod('Main',[Reflection.BindingFlags]'NonPublic,Static')
 $saved=[Console]::Out;$capture=[IO.StringWriter]::new()
 try{[Console]::SetOut($capture);$code=$main.Invoke($null,@(,[string[]]@('--install')))}finally{[Console]::SetOut($saved)}
 $refusal=$capture.ToString()|ConvertFrom-Json
 if($code -ne 2 -or $refusal.status -cne 'FAILED' -or $refusal.createdTaskDisabled -ne $false){throw 'MSI CLI wrong-host refusal differs.'}
}
$result
