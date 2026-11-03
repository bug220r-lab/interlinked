[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallDirectory,[switch]$Pair,[switch]$Revoke)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=[IO.Path]::GetFullPath($InstallDirectory)
$record=Get-Content -LiteralPath (Join-Path $root 'node.json') -Raw | ConvertFrom-Json
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if($record.format -cne 'interlinked-ai-node-install-v1' -or $record.computer -ine $env:COMPUTERNAME -or $record.sid -cne $identity.User.Value){throw 'Node installation identity differs.'}
foreach($item in @(Get-Item -LiteralPath $root)+@(Get-ChildItem -LiteralPath $root -Recurse -Force)){
    if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Linked node paths are refused.'}
}
$manifestPath=Join-Path $root 'NODE-CHECKSUMS.json'
if((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ine $record.manifestSha256){throw 'Node manifest differs.'}
$manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$members=@('interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_stats_process.py','interlinked_configuration.py','Initialize-Node.ps1','Start-Node.ps1','Set-NodeNetwork.ps1','Set-TransportRuntime.ps1','README.md')
if($manifest.format -ceq 'interlinked-ai-node-package-v2'){$members+=@('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py','Migrate-Node.ps1')}
elseif($manifest.format -cne 'interlinked-ai-node-package-v1'){throw 'Node package format differs.'}
if((@($manifest.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'files|format' -or (@($manifest.files.path | Sort-Object) -join '|') -cne (($members | Sort-Object) -join '|')){throw 'Node package member set differs.'}
foreach($entry in $manifest.files){if((@($entry.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'path|sha256' -or $entry.sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid node package member metadata.'}}
foreach($entry in $manifest.files){
    if($entry.path -notmatch '^[A-Za-z0-9_.-]+$' -or (Get-FileHash -LiteralPath (Join-Path $root $entry.path) -Algorithm SHA256).Hash -ine $entry.sha256){throw 'Node package changed.'}
}
if((Get-FileHash -LiteralPath $record.python -Algorithm SHA256).Hash -ine $record.pythonSha256){throw 'Node Python runtime changed.'}
$policyPath=$null
if($record.PSObject.Properties.Name -contains 'inferencePolicySha256'){
    if($record.inferencePolicySha256 -isnot [string] -or $record.inferencePolicySha256 -notmatch '^[a-fA-F0-9]{64}$'){throw 'Reviewed inference policy hash is invalid.'}
    $policyPath=Join-Path $root 'inference-policy.json'
    if((Get-FileHash -LiteralPath $policyPath -Algorithm SHA256).Hash -ine $record.inferencePolicySha256){throw 'Inference policy hash differs.'}
}
$lockPath=Join-Path $root '.node.lock'
$lock=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
    $arguments=@('-I','-B',(Join-Path $root 'interlinked_ai_node.py'),'--state',(Join-Path $root 'State'),'--bind',$record.bind,'--port',[string]$record.port,'--certificate',(Join-Path $root 'Secrets/node-cert.pem'),'--key',(Join-Path $root 'Secrets/node-key.pem'))
    if($Pair){$arguments+='--pair'}
    if($Revoke){$arguments+='--revoke'}
    if($null -ne $policyPath){$arguments+=@('--inference-policy',$policyPath,'--inference-policy-sha256',$record.inferencePolicySha256)}
    $checks=Join-Path $root 'reviewed-checks.json'
    if(Test-Path -LiteralPath $checks){$arguments+=@('--checks',$checks)}
    & $record.python @arguments
    exit $LASTEXITCODE
} finally {$lock.Dispose()}
