[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){if(@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0){throw 'Use a fresh empty private plugin output folder.'}}else{[void][IO.Directory]::CreateDirectory($output)}
[void][IO.Directory]::CreateDirectory((Join-Path $output '.codex-plugin'))
[void][IO.Directory]::CreateDirectory((Join-Path $output 'skills/local-ai'))
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.codex-plugin/plugin.json') -Destination (Join-Path $output '.codex-plugin/plugin.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'skills/local-ai/SKILL.md') -Destination (Join-Path $output 'skills/local-ai/SKILL.md')
$mcp=[ordered]@{mcpServers=[ordered]@{interlinked_ai=[ordered]@{command=(Join-Path $kit 'Project-Library-Client/Runtime/python.exe');args=@('-I','-B',(Join-Path $kit 'INTERLINKED-App/ai_bridge.py'),'--endpoint',(Join-Path $kit 'INTERLINKED-GUI-State/ai/bridge/endpoint.json'))}}}
[IO.File]::WriteAllText((Join-Path $output '.mcp.json'),($mcp|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
$members=[ordered]@{}
foreach($name in @('.codex-plugin/plugin.json','.mcp.json','skills/local-ai/SKILL.md')){
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try{$members[$name]=[BitConverter]::ToString($algorithm.ComputeHash([IO.File]::ReadAllBytes((Join-Path $output $name)))).Replace('-','').ToLowerInvariant()}finally{$algorithm.Dispose()}
}
[IO.File]::WriteAllText((Join-Path $output 'PLUGIN-CHECKSUMS.json'),([ordered]@{format='interlinked-ai-plugin-v1';files=$members}|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
[pscustomobject]@{Status='AI_PLUGIN_PREPARED';Directory=$output;InstalledCoordinatorRequired=$true;ActualVioletVerified=$false}|ConvertTo-Json
