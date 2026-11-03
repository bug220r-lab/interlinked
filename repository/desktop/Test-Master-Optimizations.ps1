[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$taskCompiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path -LiteralPath $taskCompiler -PathType Leaf)){throw '.NET Framework 4.8 compiler is required.'}
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
if($taskOutput.StartsWith('\\')){throw 'Use an ordinary local private fixture directory.'}
foreach($taskForbidden in @((Join-Path $env:USERPROFILE 'Documents\MSI-Project-Library'),(Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache'))){
 $taskAbsolute=[IO.Path]::GetFullPath($taskForbidden).TrimEnd('\')
 if($taskOutput.Equals($taskAbsolute,[StringComparison]::OrdinalIgnoreCase)-or $taskOutput.StartsWith($taskAbsolute+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Synthetic optimization checks must stay outside the production kit/cache.'}
}
if(Test-Path -LiteralPath $taskOutput){throw 'Use an absent private fixture output directory.'}
$taskAncestor=[IO.Path]::GetDirectoryName($taskOutput)
while($taskAncestor){
 if((Test-Path -LiteralPath $taskAncestor)-and (([int][IO.File]::GetAttributes($taskAncestor)-band 0x441400)-ne 0)){throw 'Fixture ancestors must not be links, cloud placeholders or offline paths.'}
 $taskAncestor=[IO.Path]::GetDirectoryName($taskAncestor)
}
[void][IO.Directory]::CreateDirectory($taskOutput)
$taskSources=@((Join-Path $PSScriptRoot 'MasterWorker.cs'),(Join-Path $PSScriptRoot 'MasterOptimizationTests.cs'))
$taskBefore=@($taskSources|ForEach-Object{[ordered]@{Path=$_;Sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}})
$taskExe=Join-Path $taskOutput 'INTERLINKED.Master.OptimizationTests.exe'
$taskArguments=@('/nologo','/target:exe','/main:InterlinkedMaster.MasterOptimizationTests','/platform:x64','/optimize+','/warn:4',('/out:'+$taskExe),'/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll')+$taskSources
$taskCompile=& $taskCompiler @taskArguments 2>&1|Out-String
$taskCompileExit=$LASTEXITCODE
$taskCompile|Set-Content -LiteralPath (Join-Path $taskOutput 'compile.txt') -Encoding utf8
if($taskCompileExit-ne 0){throw 'Synthetic optimization fixture compilation failed; private diagnostics are preserved.'}
for($taskIndex=0;$taskIndex-lt $taskSources.Count;$taskIndex++){
 if((Get-FileHash -LiteralPath $taskSources[$taskIndex] -Algorithm SHA256).Hash.ToLowerInvariant()-cne $taskBefore[$taskIndex].Sha256){throw 'A source input changed during compilation; inspect and rerun in a fresh fixture directory.'}
}
[IO.File]::WriteAllText(($taskExe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
$taskReceipt=[ordered]@{Format='interlinked-master-optimization-build-v1';BuiltUtc=[DateTime]::UtcNow.ToString('o');Compiler=$taskCompiler;CompilerSha256=(Get-FileHash -LiteralPath $taskCompiler -Algorithm SHA256).Hash.ToLowerInvariant();Arguments=$taskArguments;Sources=$taskBefore;FixtureSha256=(Get-FileHash -LiteralPath $taskExe -Algorithm SHA256).Hash.ToLowerInvariant();CompileExit=$taskCompileExit;HardwareTested=$false;Scope='Synthetic sampler delegates, in-memory bitmaps and frame drawing only; production Worker.Run, vendor USB, sensors, capture and elevation are not invoked.'}
$taskReceipt|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $taskOutput 'build-receipt.json') -Encoding utf8
& $taskExe (Join-Path $taskOutput 'checks')
if($LASTEXITCODE-ne 0){throw 'Synthetic optimization checks failed; private fixtures are preserved.'}
