[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Choose a fresh absent synthetic output directory.'}
if($output.StartsWith('C:\Users\Jude\Documents\MSI-Project-Library',[StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith((Join-Path $env:LOCALAPPDATA 'ProjectLibrary'),[StringComparison]::OrdinalIgnoreCase)){throw 'Synthetic tests must stay outside production.'}
New-Item -ItemType Directory -Path $output | Out-Null
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$exe=Join-Path $output 'MasterTrayTests.exe'
& $compiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll ('/out:'+$exe) (Join-Path $PSScriptRoot 'MasterTray.cs') (Join-Path $PSScriptRoot 'MasterTrayTests.cs')
if($LASTEXITCODE -ne 0){throw 'MASTER tray test compilation failed.'}
& $exe
if($LASTEXITCODE -ne 0){throw 'MASTER tray lifecycle checks failed.'}
