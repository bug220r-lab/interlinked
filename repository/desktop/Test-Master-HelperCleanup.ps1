[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path -LiteralPath $compiler -PathType Leaf)){throw '.NET Framework 4.8 compiler is required.'}
$output=[IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
if($output.StartsWith('\\')){throw 'Use an ordinary local private fixture directory.'}
foreach($forbidden in @((Join-Path $env:USERPROFILE 'Documents\MSI-Project-Library'),(Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache'))){
 $absolute=[IO.Path]::GetFullPath($forbidden).TrimEnd('\')
 if($output.Equals($absolute,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($absolute+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Synthetic helper checks must stay outside the production kit/cache.'}
}
if(Test-Path -LiteralPath $output){throw 'Use an absent private fixture output directory.'}
$ancestor=[IO.Path]::GetDirectoryName($output)
while($ancestor){
 if((Test-Path -LiteralPath $ancestor)-and (([int][IO.File]::GetAttributes($ancestor)-band 0x441400)-ne 0)){throw 'Fixture ancestors must not be links, cloud placeholders or offline paths.'}
 $ancestor=[IO.Path]::GetDirectoryName($ancestor)
}
[void][IO.Directory]::CreateDirectory($output)
$sources=@((Join-Path $PSScriptRoot 'MasterWorker.cs'),(Join-Path $PSScriptRoot 'MasterHelperCleanupTests.cs'))
$before=@($sources|ForEach-Object{[ordered]@{Path=$_;Sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}})
$exe=Join-Path $output 'INTERLINKED.Master.HelperCleanupTests.exe'
$arguments=@('/nologo','/target:exe','/main:InterlinkedMaster.MasterHelperCleanupTests','/platform:x64','/optimize+','/warn:4',('/out:'+$exe),'/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll')+$sources
$compile=& $compiler @arguments 2>&1|Out-String
$compileExit=$LASTEXITCODE
$compile|Set-Content -LiteralPath (Join-Path $output 'compile.txt') -Encoding utf8
if($compileExit-ne 0){throw 'Synthetic cleanup fixture compilation failed; diagnostics preserved.'}
for($i=0;$i-lt $sources.Count;$i++){
 if((Get-FileHash -LiteralPath $sources[$i] -Algorithm SHA256).Hash.ToLowerInvariant()-cne $before[$i].Sha256){throw 'A source input changed during compilation; inspect and rerun in a fresh fixture directory.'}
}
[IO.File]::WriteAllText(($exe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
$receipt=[ordered]@{Format='interlinked-master-helper-cleanup-build-v1';BuiltUtc=[DateTime]::UtcNow.ToString('o');Compiler=$compiler;CompilerSha256=(Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant();Arguments=$arguments;Sources=$before;FixtureSha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant();CompileExit=$compileExit;HardwareTested=$false;Scope='Synthetic fixture entry point and its private child processes/maps only; production Worker.Main/Run, vendor USB, sensors and elevation are not invoked.'}
$receipt|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $output 'build-receipt.json') -Encoding utf8
& $exe (Join-Path $output 'checks')
if($LASTEXITCODE-ne 0){throw 'Synthetic helper cleanup checks failed; private fixtures preserved.'}
