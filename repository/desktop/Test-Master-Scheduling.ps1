[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$taskCompiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path -LiteralPath $taskCompiler -PathType Leaf)){throw '.NET Framework 4.8 compiler is required.'}
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
if($taskOutput.StartsWith('\\')){throw 'Use an ordinary local private fixture directory.'}
$taskProfile=[IO.Path]::GetFullPath($env:USERPROFILE)
$taskForbidden=@((Join-Path $taskProfile 'Documents\MSI-Project-Library'),
    (Join-Path $taskProfile 'AppData\Local\ProjectLibrary\Cache'),
    (Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache'))
foreach($taskPath in $taskForbidden){
    $taskAbsolute=[IO.Path]::GetFullPath($taskPath).TrimEnd('\')
    if($taskOutput.Equals($taskAbsolute,[StringComparison]::OrdinalIgnoreCase)-or $taskOutput.StartsWith($taskAbsolute+'\',[StringComparison]::OrdinalIgnoreCase)){
        throw 'Synthetic scheduling checks must stay outside the production kit and working cache.'
    }
}
if(Test-Path -LiteralPath $taskOutput){throw 'Use an absent private fixture output directory.'}
$taskAncestor=[IO.Path]::GetDirectoryName($taskOutput)
while($taskAncestor){
    if(Test-Path -LiteralPath $taskAncestor){
        $taskAttributes=[int][IO.File]::GetAttributes($taskAncestor)
        if(($taskAttributes-band (0x400-bor 0x1000-bor 0x40000-bor 0x400000))-ne 0){throw 'Fixture ancestors must not be links, cloud placeholders or offline paths.'}
    }
    $taskAncestor=[IO.Path]::GetDirectoryName($taskAncestor)
}
[void][IO.Directory]::CreateDirectory($taskOutput)
$taskSources=@((Join-Path $PSScriptRoot 'MasterWorker.cs'),(Join-Path $PSScriptRoot 'MasterSchedulingTests.cs'))
$taskBefore=@($taskSources | ForEach-Object {[ordered]@{Path=$_;Sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}})
$taskExe=Join-Path $taskOutput 'INTERLINKED.Master.SchedulingTests.exe'
$taskArgs=@('/nologo','/target:exe','/main:InterlinkedMaster.MasterSchedulingTests','/platform:x64','/optimize+','/warn:4',('/out:'+$taskExe),'/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll')+$taskSources
$taskCompilerOutput=& $taskCompiler @taskArgs 2>&1 | Out-String
$taskCompilerExit=$LASTEXITCODE
$taskCompilerOutput | Set-Content -LiteralPath (Join-Path $taskOutput 'compile.txt') -Encoding utf8
if($taskCompilerExit-ne 0){throw 'Synthetic scheduling fixture compilation failed; private diagnostics are preserved.'}
for($taskIndex=0;$taskIndex-lt $taskSources.Count;$taskIndex++){
    if((Get-FileHash -LiteralPath $taskSources[$taskIndex] -Algorithm SHA256).Hash.ToLowerInvariant()-ne $taskBefore[$taskIndex].Sha256){throw 'A source input changed during compilation; inspect and rerun in a fresh fixture directory.'}
}
[IO.File]::WriteAllText(($taskExe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
$taskReceipt=[ordered]@{Format='interlinked-master-scheduling-build-v1';BuiltUtc=[DateTime]::UtcNow.ToString('o');Compiler=$taskCompiler;
    CompilerSha256=(Get-FileHash -LiteralPath $taskCompiler -Algorithm SHA256).Hash.ToLowerInvariant();Arguments=$taskArgs;Sources=$taskBefore;
    FixtureSha256=(Get-FileHash -LiteralPath $taskExe -Algorithm SHA256).Hash.ToLowerInvariant();CompileExit=$taskCompilerExit;HardwareTested=$false;
    Scope='Synthetic fixture entry point only; production Worker.Main/Run, USB, sensors and capture are not invoked.'}
$taskReceipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $taskOutput 'build-receipt.json') -Encoding utf8
& $taskExe (Join-Path $taskOutput 'checks')
if($LASTEXITCODE-ne 0){throw 'Synthetic scheduling checks failed; private fixtures are preserved.'}
