# Pure source-extracted fake status tests. No app/SAPI/microphone is launched.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
$kit=[IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'Documents\MSI-Project-Library')).TrimEnd('\')
$cache=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache')).TrimEnd('\')
foreach($forbidden in @($kit,$cache)){if($output-eq$forbidden-or$output.StartsWith($forbidden+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Use a new private fixture folder outside kit/cache.'}}
if(Test-Path -LiteralPath $output){throw 'Preserve earlier evidence; output must be absent.'}
$source=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Interlinked.cs'))
$matches=[regex]::Matches($source,'(?s)// BEGIN PURE AI SPEECH STATUS(.*?)// END PURE AI SPEECH STATUS')
if($matches.Count-ne1){throw 'Exactly one native status/fence test region required.'}
[IO.Directory]::CreateDirectory($output)|Out-Null
$pure=Join-Path $output 'AiSpeechStatus.extracted.cs'
[IO.File]::WriteAllText($pure,"using System;`nnamespace InterlinkedDesktop {`n"+$matches[0].Groups[1].Value+"`n}",[Text.UTF8Encoding]::new($false))
$exe=Join-Path $output 'AiSpeechActivityTests.exe'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo /target:exe /platform:x64 /optimize+ ('/out:'+$exe) $pure (Join-Path $PSScriptRoot 'AiSpeechActivityTests.cs')
if($LASTEXITCODE-ne0){throw 'Pure native voice-status fixture did not compile.'}
& $exe | Tee-Object -FilePath (Join-Path $output 'results.txt')
if($LASTEXITCODE-ne0){throw 'Pure native voice-status fixture failed.'}
