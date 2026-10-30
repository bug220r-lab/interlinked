[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(-not (Test-Path -LiteralPath $compiler -PathType Leaf)){throw '.NET Framework 4.8 compiler is required.'}
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){if(@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0){throw 'Use a fresh empty build output directory.'}}else{[void][IO.Directory]::CreateDirectory($output)}
$exe=Join-Path $output 'INTERLINKED.Master.Worker.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /warn:4 ('/out:'+$exe) /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'MasterWorker.cs')
if($LASTEXITCODE -ne 0){throw 'MASTER worker compilation failed.'}
[IO.File]::WriteAllText(($exe+'.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
$files=[ordered]@{}
foreach($name in @('INTERLINKED.Master.Worker.exe','INTERLINKED.Master.Worker.exe.config')){$files[$name]=(Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant()}
[pscustomobject]@{Status='MASTER_WORKER_BUILT';OutputDirectory=$output;Files=$files}|ConvertTo-Json -Depth 4
