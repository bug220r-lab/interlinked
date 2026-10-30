[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,[string]$WorkerSource,[string]$VioletAtlas)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
foreach($forbidden in @('C:\Users\Jude\Documents\MSI-Project-Library', (Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache'))){
    $path=[IO.Path]::GetFullPath($forbidden).TrimEnd('\')
    if($output.Equals($path,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Use a synthetic fixture outside the installed kit/cache.'}
}
if(Test-Path -LiteralPath $output){throw 'Use a fresh fixture output directory.'}
[void][IO.Directory]::CreateDirectory($output)
if(-not $WorkerSource){$WorkerSource=Join-Path $PSScriptRoot 'MasterWorker.cs'}
$source=[IO.Path]::GetFullPath($WorkerSource)
$exe=Join-Path $output 'INTERLINKED.VioletStatusFixture.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /main:InterlinkedMaster.VioletStatusFixture /platform:x64 /optimize+ /warn:4 ('/out:'+$exe) /reference:System.Drawing.dll /reference:System.Web.Extensions.dll $source (Join-Path $PSScriptRoot 'VioletStatusFixture.cs')
if($LASTEXITCODE-ne 0){throw 'Violet status fixture compilation failed.'}
$arguments=@((Join-Path $output 'rendered'));if($VioletAtlas){$arguments+=[IO.Path]::GetFullPath($VioletAtlas)}
& $exe @arguments
if($LASTEXITCODE-ne 0){throw 'Violet status regression failed.'}
