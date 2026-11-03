[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$AIStateDirectory,[Parameter(Mandatory=$true)][string]$Python,[Parameter(Mandatory=$true)][string]$PythonSha256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=(Resolve-Path -LiteralPath $AIStateDirectory).Path
$runtime=(Resolve-Path -LiteralPath $Python).Path
foreach($path in @($root,$runtime)){
    $item=Get-Item -LiteralPath $path
    while($item){
        if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Linked transport paths are refused.'}
        $item=if($item -is [IO.DirectoryInfo]){$item.Parent}else{$item.Directory}
    }
}
if((Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash -ine $PythonSha256){throw 'Reviewed transport runtime differs.'}
if(-not (Test-Path -LiteralPath (Join-Path $root 'OWNER.json'))){throw 'Use the AI state directory already created by the reviewed application.'}
$path=Join-Path $root 'transport-runtime.json'
if(Test-Path -LiteralPath $path){throw 'Existing transport configuration is preserved; review it before replacement.'}
& $runtime -I -B -c 'import ssl; c=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); print(''TLS_CONTEXT_READY'')'
if($LASTEXITCODE -ne 0){throw 'This runtime cannot initialize TLS.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
[ordered]@{format='interlinked-ai-transport-v1';python=$runtime;sha256=$PythonSha256.ToLowerInvariant();identity=[ordered]@{computer=$env:COMPUTERNAME;sid=$identity.User.Value}} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding UTF8
Write-Output 'Reviewed per-user TLS transport configured. Restart the interface to load it.'
