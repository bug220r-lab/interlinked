[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$output=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $output){throw 'Choose a new output directory.'}
[IO.Directory]::CreateDirectory($output) | Out-Null
$app=Join-Path (Split-Path -Parent $PSScriptRoot) 'app'
$files=@()
foreach($name in @('interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_stats_process.py','interlinked_configuration.py','interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py')){
    Copy-Item -LiteralPath (Join-Path $app $name) -Destination (Join-Path $output $name)
    $files+=@([ordered]@{path=$name;sha256=(Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant()})
}
foreach($name in @('Initialize-Node.ps1','Start-Node.ps1','Set-NodeNetwork.ps1','Set-TransportRuntime.ps1','README.md','Migrate-Node.ps1')){
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $output $name)
    $files+=@([ordered]@{path=$name;sha256=(Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant()})
}
$manifest=Join-Path $output 'NODE-CHECKSUMS.json'
[ordered]@{format='interlinked-ai-node-package-v2';files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifest -Encoding UTF8
Get-FileHash -LiteralPath $manifest -Algorithm SHA256
