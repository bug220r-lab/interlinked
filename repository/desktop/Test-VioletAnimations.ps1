[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PackageRoot,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Use an absent isolated test directory.'}
[IO.Directory]::CreateDirectory($output)|Out-Null
$resource=Join-Path $output 'VioletAnimations.bin'
& (Join-Path $PSScriptRoot 'New-VioletAnimationBundle.ps1') -PackageRoot $PackageRoot -OutputPath $resource | Out-Null
$locked=[IO.File]::Open($resource,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
 $exe=Join-Path $output 'VioletAnimationTests.exe'
 & 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /main:InterlinkedMaster.VioletAnimationTests /platform:x64 /optimize+ /warn:4 ('/out:'+$exe) /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ('/resource:'+$resource+',Interlinked.VioletAnimations.v1') (Join-Path $PSScriptRoot 'MasterWorker.cs') (Join-Path $PSScriptRoot 'VioletAnimationTests.cs')
 if($LASTEXITCODE-ne0){throw 'Animation checks did not compile.'}
 & $exe (Join-Path $output 'checks')
 if($LASTEXITCODE-ne0){throw 'Animation checks failed.'}
}finally{$locked.Dispose()}
