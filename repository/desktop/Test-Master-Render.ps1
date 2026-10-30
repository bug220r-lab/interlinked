[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$VioletAtlas
)
$ErrorActionPreference='Stop'
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not(Test-Path -LiteralPath $compiler -PathType Leaf)){throw '.NET Framework 4.8 compiler is required.'}
$output=[IO.Path]::GetFullPath($OutputDirectory)
$kit=[IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'Documents\MSI-Project-Library')).TrimEnd('\')
$cache=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache')).TrimEnd('\')
foreach($forbidden in @($kit,$cache)){
    if($output.Equals($forbidden,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($forbidden+'\',[StringComparison]::OrdinalIgnoreCase)){
        throw 'Synthetic rendering checks must stay outside the production kit and working cache.'
    }
}
if(Test-Path -LiteralPath $output){throw 'Use an absent private fixture output directory.'}
[void][IO.Directory]::CreateDirectory($output)
$exe=Join-Path $output 'INTERLINKED.Master.RenderFixture.exe'
& $compiler /nologo /target:exe /main:InterlinkedMaster.MasterRenderFixture /platform:x64 /optimize+ /warn:4 ('/out:'+$exe) /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'MasterWorker.cs') (Join-Path $PSScriptRoot 'MasterRenderFixture.cs')
if($LASTEXITCODE-ne 0){throw 'Synthetic renderer fixture compilation failed.'}
[IO.File]::WriteAllText(($exe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
$arguments=@((Join-Path $output 'rendered'))
if($VioletAtlas){$arguments+=[IO.Path]::GetFullPath($VioletAtlas)}
& $exe @arguments
if($LASTEXITCODE-ne 0){throw 'Synthetic renderer checks failed; fixture files are preserved.'}
