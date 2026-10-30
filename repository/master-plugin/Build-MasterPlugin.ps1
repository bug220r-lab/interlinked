[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){if(@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0){throw 'Use a fresh empty private plugin output folder.'}}else{[void][IO.Directory]::CreateDirectory($output)}
[void][IO.Directory]::CreateDirectory((Join-Path $output '.codex-plugin'))
[void][IO.Directory]::CreateDirectory((Join-Path $output 'skills/master-status'))
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.codex-plugin/plugin.json') -Destination (Join-Path $output '.codex-plugin/plugin.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'skills/master-status/SKILL.md') -Destination (Join-Path $output 'skills/master-status/SKILL.md')
$mcp=[ordered]@{mcpServers=[ordered]@{interlinked_master=[ordered]@{command=(Join-Path $kit 'Project-Library-Client/Runtime/python.exe');args=@('-I','-B',(Join-Path $kit 'INTERLINKED-App/master_bridge.py'),'--state-root','C:\Users\Jude\AppData\Local\INTERLINKED\MASTER-status')}}}
[IO.File]::WriteAllText((Join-Path $output '.mcp.json'),($mcp|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
[pscustomobject]@{Status='MASTER_PLUGIN_PREPARED';Directory=$output;InstalledRuntimeRequired=$true;ActualDotVerified=$false}|ConvertTo-Json
