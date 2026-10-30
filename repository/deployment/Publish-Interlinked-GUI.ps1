# Add reviewed GUI files without changing active workspaces or the accepted launcher.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ManifestPath,[Parameter(Mandatory=$true)][string]$ManifestSha256,[Parameter(Mandatory=$true)][string]$ReceiptDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$workspace='C:\Users\jlewi\OneDrive\Documents\ChatGPT\INTERLINKED'
$kit='\\192.168.247.2\Users\Jude\Documents\MSI-Project-Library'
$allowed=@('INTERLINKED.cmd','INTERLINKED-START-HERE.txt','MAGIX-Media-Audit.json','MAGIX-Media-Audit.html','INTERLINKED-App/interlinked_gui.py','INTERLINKED-App/web/index.html','INTERLINKED-App/web/app.js','INTERLINKED-App/web/style.css','INTERLINKED-App/GUI-CHECKSUMS.json')
$pins=@{'magix_projects.py'='6c2ea58d9ac89a20c4b2bb42347b79f5802b049b2c81812314e09412578de1bf';'magix_workspace.py'='c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa';'MAGIX-All-Projects-Index.json'='bccb71983d23a3baa88926aca4d14fe6ce95d003e46e1d3d696350e47b746a83'}
$run=[guid]::NewGuid().ToString('N')
$guiLock=$null
function Digest([string]$Path) {(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Ordinary([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw ('Reparse path refused: '+$Path)}
}
$receiptRoot=[IO.Path]::GetFullPath($ReceiptDirectory)
if(-not $receiptRoot.StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Receipts must stay in this workspace.'}
Ordinary $receiptRoot
$receiptPath=Join-Path $receiptRoot ('publication-'+$run+'.json')
$receipt=[ordered]@{format='interlinked-gui-publication-receipt-v1';status='PREFLIGHT';startedUtc=[DateTime]::UtcNow.ToString('o');runId=$run;files=@();projectFilesWritten=$false;workspaceStateWritten=$false;failure=$null}
function Save-Receipt {$receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding utf8}
try {
    if((Digest $ManifestPath) -cne $ManifestSha256.ToLowerInvariant()){throw 'Publication plan changed.'}
    $manifest=Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if($manifest.format -cne 'interlinked-gui-publication-v1'){throw 'Unexpected publication plan.'}
    Ordinary $kit
    $guiLockPath=Join-Path $kit 'INTERLINKED-GUI-State/.gui-instance.lock'
    if(Test-Path -LiteralPath $guiLockPath){
        Ordinary ([IO.Path]::GetDirectoryName($guiLockPath));Ordinary $guiLockPath
        $guiLock=[IO.FileStream]::new($guiLockPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
    }
    foreach($name in $pins.Keys){Ordinary (Join-Path $kit $name);if((Digest (Join-Path $kit $name)) -cne $pins[$name]){throw ('Accepted launcher changed: '+$name)}}
    $seen=@{}
    foreach($row in $manifest.files){
        if($row.path -cnotin $allowed -or $seen.ContainsKey($row.path)){throw 'Unknown or duplicate target.'}
        $seen[$row.path]=$true
        $source=[IO.Path]::GetFullPath($row.source)
        if(-not $source.StartsWith($workspace+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Source is outside workspace.'}
        Ordinary $source
        if((Digest $source) -cne $row.sha256){throw ('Reviewed source changed: '+$row.path)}
        $target=Join-Path $kit $row.path
        if(Test-Path -LiteralPath $target){
            Ordinary $target
            if(-not $row.beforeSha256 -or (Digest $target) -cne $row.beforeSha256){throw ('Existing target differs: '+$row.path)}
        } elseif($row.beforeSha256){throw ('Expected target absent: '+$row.path)}
    }
    if($seen.Count -ne $allowed.Count){throw 'Incomplete GUI publication plan.'}
    Save-Receipt
    foreach($folder in @('INTERLINKED-App','INTERLINKED-App/web')){
        $path=Join-Path $kit $folder
        if(-not (Test-Path -LiteralPath $path)){[IO.Directory]::CreateDirectory($path) | Out-Null}
        Ordinary $path
    }
    # Supporting resources are installed before the new command becomes visible.
    foreach($row in ($manifest.files | Sort-Object @{Expression={if($_.path -ceq 'INTERLINKED.cmd'){2}elseif($_.path -like '*GUI-CHECKSUMS.json'){1}else{0}}},path)){
        $target=Join-Path $kit $row.path
        Ordinary ([IO.Path]::GetDirectoryName($target))
        $temp=$target+'.pending-'+$run
        if(Test-Path -LiteralPath $temp){throw 'Staging file exists.'}
        Copy-Item -LiteralPath $row.source -Destination $temp
        if((Digest $temp) -cne $row.sha256){throw 'Staged bytes differ.'}
        $backup=$null
        if($row.beforeSha256){
            Ordinary $target
            if((Digest $target) -cne $row.beforeSha256){throw 'Existing target changed during publication.'}
            $backup=$target+'.before-gui-'+$run
            [IO.File]::Replace($temp,$target,$backup)
            if((Digest $backup) -cne $row.beforeSha256){throw 'Previous bytes were not preserved.'}
        }else{
            if(Test-Path -LiteralPath $target){throw 'New target appeared during publication.'}
            [IO.File]::Move($temp,$target)
        }
        if((Digest $target) -cne $row.sha256){throw 'Published bytes differ.'}
        $receipt.files+=@([ordered]@{path=$row.path;sha256=$row.sha256;backup=$backup})
        Save-Receipt
    }
    foreach($name in $pins.Keys){if((Digest (Join-Path $kit $name)) -cne $pins[$name]){throw ('Accepted launcher changed during publication: '+$name)}}
    $receipt.status='GUI_AND_AUDIT_PUBLISHED_HASH_VERIFIED'
    $receipt.acceptedLauncherUnchanged=$true
}catch{$receipt.status='PUBLICATION_STOPPED';$receipt.failure=$_.Exception.Message;throw}
finally{if($guiLock){$guiLock.Dispose()};$receipt.finishedUtc=[DateTime]::UtcNow.ToString('o');Save-Receipt;Write-Output $receiptPath}
