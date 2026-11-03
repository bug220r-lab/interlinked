# Reviewed, stopped-node package migration. Never starts/stops tasks or inference.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [Parameter(Mandatory=$true)][string]$CandidateDirectory,
    [Parameter(Mandatory=$true)][string]$ExpectedBeforeManifestSha256,
    [Parameter(Mandatory=$true)][string]$ExpectedAfterManifestSha256,
    [Parameter(Mandatory=$true)][string]$ReceiptDirectory,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Ordinary([string]$Path,[switch]$Missing){
    $full=[IO.Path]::GetFullPath($Path)
    if($full.StartsWith('\\')){throw 'Migration requires ordinary local paths.'}
    if($full.Length -gt 259){throw 'Migration path exceeds the Windows PowerShell limit; choose shorter package and receipt directories.'}
    $walk=$full
    while($walk){
        if(Test-Path -LiteralPath $walk){if(([long](Get-Item -LiteralPath $walk -Force).Attributes -band 0x441400) -ne 0){throw 'Linked/cloud/offline migration paths are refused.'}}
        elseif(-not $Missing){throw 'Required migration path is absent.'}
        $parent=[IO.Path]::GetDirectoryName($walk);if($parent -eq $walk){break};$walk=$parent
    }
    return $full
}
if(-not ('InterlinkedNodeMigrationNative' -as [type])){
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class InterlinkedNodeMigrationNative {
 [StructLayout(LayoutKind.Sequential)] public struct Info { public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
 [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
 public static uint Links(SafeFileHandle handle) { Info info; if(!GetFileInformationByHandle(handle,out info))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());return info.Links; }
}
'@
}
function ReadBytes([string]$Path){
    $null=Ordinary $Path
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        if([InterlinkedNodeMigrationNative]::Links($stream.SafeFileHandle) -ne 1){throw 'Hardlinked migration files are refused.'}
        if($stream.Length -gt 33554432){throw 'Migration metadata or member exceeds 32 MiB.'}
        $bytes=New-Object byte[] ([int]$stream.Length);$offset=0
        while($offset -lt $bytes.Length){$n=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($n -eq 0){throw 'Short migration read.'};$offset+=$n}
        return ,$bytes
    }finally{$stream.Dispose()}
}
function Digest([byte[]]$Bytes){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function JsonBytes($Value){return ,[Text.UTF8Encoding]::new($false).GetBytes(($Value|ConvertTo-Json -Depth 30))}
function Parse([byte[]]$Bytes){[Text.Encoding]::UTF8.GetString($Bytes).TrimStart([char]0xfeff)|ConvertFrom-Json}
function Keys($Value,[string[]]$Expected){if((@($Value.PSObject.Properties.Name|Sort-Object)-join '|') -cne (($Expected|Sort-Object)-join '|')){throw 'Migration metadata schema differs.'}}
function Package([string]$Directory,[string]$Expected,[switch]$Candidate){
    $raw=ReadBytes (Join-Path $Directory 'NODE-CHECKSUMS.json')
    if((Digest $raw) -cne $Expected){throw 'Reviewed package manifest hash differs.'}
    $manifest=Parse $raw;Keys $manifest @('format','files')
    $names=@('interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_stats_process.py','interlinked_configuration.py','Initialize-Node.ps1','Start-Node.ps1','Set-NodeNetwork.ps1','Set-TransportRuntime.ps1','README.md')
    if($manifest.format -ceq 'interlinked-ai-node-package-v2'){$names+=@('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py','Migrate-Node.ps1')}
    elseif($manifest.format -cne 'interlinked-ai-node-package-v1' -or $Candidate){throw 'Unsupported migration package version.'}
    if((@($manifest.files.path|Sort-Object)-join '|') -cne (($names|Sort-Object)-join '|')){throw 'Migration package member set differs.'}
    $buffers=@{'NODE-CHECKSUMS.json'=$raw}
    foreach($entry in $manifest.files){
        Keys $entry @('path','sha256')
        if($entry.sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid package digest.'}
        $bytes=ReadBytes (Join-Path $Directory $entry.path)
        if((Digest $bytes) -cne $entry.sha256){throw 'Package member differs from seal.'}
        $buffers[$entry.path]=$bytes
    }
    return @{manifest=$manifest;buffers=$buffers}
}
function WriteAtomic([string]$Path,[byte[]]$Bytes){
    $null=Ordinary $Path -Missing
    $temp=Join-Path ([IO.Path]::GetDirectoryName($Path)) ('.m-'+[guid]::NewGuid().ToString('N').Substring(0,16))
    $null=Ordinary $temp -Missing
    $stream=[IO.File]::Open($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    if(Test-Path -LiteralPath $Path){$null=ReadBytes $Path;[IO.File]::Replace($temp,$Path,[NullString]::Value)}else{[IO.File]::Move($temp,$Path)}
    if((Digest (ReadBytes $Path)) -cne (Digest $Bytes)){throw 'Migration readback differs.'}
}
$root=Ordinary $InstallDirectory;$candidate=Ordinary $CandidateDirectory;$receipts=Ordinary $ReceiptDirectory
foreach($pair in @(@($root,$candidate),@($root,$receipts),@($candidate,$receipts))){
    if($pair[0].TrimEnd('\') -ieq $pair[1].TrimEnd('\') -or $pair[0].StartsWith($pair[1].TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or $pair[1].StartsWith($pair[0].TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Install, candidate and receipt directories must be separate.'}
}
foreach($pin in @($ExpectedBeforeManifestSha256,$ExpectedAfterManifestSha256)){if($pin -cnotmatch '^[a-f0-9]{64}$'){throw 'Use exact reviewed lowercase SHA256 manifest pins.'}}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run migration as the installed ordinary user, without elevation.'}
$configBytes=ReadBytes (Join-Path $root 'node.json');$record=Parse $configBytes
if($record.format -cne 'interlinked-ai-node-install-v1' -or $record.computer -ine $env:COMPUTERNAME -or $record.sid -cne $identity.User.Value -or $record.manifestSha256 -cne $ExpectedBeforeManifestSha256){throw 'Installed node identity or manifest pin differs.'}
$null=Ordinary $record.python
if((Get-FileHash -LiteralPath $record.python -Algorithm SHA256).Hash -ine $record.pythonSha256){throw 'Installed interpreter pin differs.'}
$before=Package $root $ExpectedBeforeManifestSha256;$after=Package $candidate $ExpectedAfterManifestSha256 -Candidate
$retained=@{}
foreach($name in @('Secrets/node-cert.pem','Secrets/node-key.pem','reviewed-checks.json','inference-policy.json')){
    $path=Join-Path $root $name
    if(Test-Path -LiteralPath $path){$retained[$name]=Digest (ReadBytes $path)}else{$retained[$name]=$null}
}
if(-not $retained['Secrets/node-cert.pem'] -or -not $retained['Secrets/node-key.pem']){throw 'Installed TLS identity is incomplete.'}
if($record.PSObject.Properties.Name -contains 'inferencePolicySha256'){
    if($record.inferencePolicySha256 -cnotmatch '^[a-f0-9]{64}$' -or $retained['inference-policy.json'] -cne $record.inferencePolicySha256){throw 'Reviewed inference policy differs.'}
}
$locks=New-Object System.Collections.Generic.List[IDisposable]
$receiptPath=$null;$written=New-Object System.Collections.Generic.List[string]
try{
    # Both the PowerShell launcher and direct Python entry point must be idle.
    foreach($name in @('.node.lock','.ai-node-instance.lock')){
        $path=Join-Path $root $name;$null=Ordinary $path -Missing
        if(Test-Path -LiteralPath $path){$null=ReadBytes $path}
        $handle=[IO.File]::Open($path,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        $locks.Add($handle)
    }
    # Revalidate after acquiring both instance locks; keep source bytes in memory.
    $null=Package $root $ExpectedBeforeManifestSha256
    if((Digest (ReadBytes (Join-Path $root 'node.json'))) -cne (Digest $configBytes)){throw 'Node identity changed during preflight.'}
    foreach($name in $after.buffers.Keys){if(-not $before.buffers.ContainsKey($name) -and (Test-Path -LiteralPath (Join-Path $root $name))){throw 'Unowned new package target is preserved.'}}
    $result=[ordered]@{format='interlinked-ai-node-migration-v1';status='PREFLIGHT_PASSED';computer=$record.computer;sid=$record.sid;beforeManifestSha256=$ExpectedBeforeManifestSha256;afterManifestSha256=$ExpectedAfterManifestSha256;files=$after.manifest.files.Count;retained=$retained;stateWritten=$false;runtimeWritten=$false;processesStarted=$false;processesStopped=$false}
    if(-not $Apply){[pscustomobject]$result;return}
    $run=Join-Path $receipts ('migration-'+[guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($run)
    $backup=Join-Path $run 'before';[void][IO.Directory]::CreateDirectory($backup)
    foreach($name in $before.buffers.Keys){WriteAtomic (Join-Path $backup $name) $before.buffers[$name]}
    WriteAtomic (Join-Path $backup 'node.json') $configBytes
    $receiptPath=Join-Path $run 'receipt.json';$result.status='APPLYING';WriteAtomic $receiptPath (JsonBytes $result)
    $record.manifestSha256=$ExpectedAfterManifestSha256
    $newConfig=JsonBytes $record
    $targets=@($after.manifest.files.path)+@('NODE-CHECKSUMS.json','node.json')
    foreach($name in $targets){
        $target=Join-Path $root $name
        $old=if($name -ceq 'node.json'){$configBytes}elseif($before.buffers.ContainsKey($name)){$before.buffers[$name]}else{$null}
        if($null -ne $old){if((Digest (ReadBytes $target)) -cne (Digest $old)){throw 'Installed target changed before replacement.'}}
        elseif(Test-Path -LiteralPath $target){throw 'New target appeared during migration.'}
        $bytes=if($name -ceq 'node.json'){$newConfig}else{$after.buffers[$name]}
        $written.Add($name);WriteAtomic $target $bytes
    }
    $null=Package $root $ExpectedAfterManifestSha256 -Candidate
    foreach($name in $retained.Keys){$path=Join-Path $root $name;$hash=if(Test-Path -LiteralPath $path){Digest (ReadBytes $path)}else{$null};if($hash -cne $retained[$name]){throw 'Retained identity or policy changed during migration.'}}
    $result.status='NODE_MIGRATED_HASH_VERIFIED';WriteAtomic $receiptPath (JsonBytes $result)
    $result.receipt=$receiptPath;[pscustomobject]$result
}catch{
    if($written.Count){
        $rollback=$true
        foreach($name in @($written.ToArray() | Select-Object -Last $written.Count)){
            try{
                $path=Join-Path $root $name
                $expected=if($name -ceq 'node.json'){$newConfig}else{$after.buffers[$name]}
                $old=if($name -ceq 'node.json'){$configBytes}elseif($before.buffers.ContainsKey($name)){$before.buffers[$name]}else{$null}
                if(Test-Path -LiteralPath $path){$actual=Digest (ReadBytes $path);if($null -ne $old -and $actual -ceq (Digest $old)){continue};if($actual -cne (Digest $expected)){throw 'Rollback target has foreign changes.'}}
                if($null -ne $old){WriteAtomic $path $old}elseif(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path}
            }catch{$rollback=$false}
        }
        $result.status=if($rollback){'FAILED_ROLLED_BACK'}else{'FAILED_RECOVERY_REQUIRED'}
        if($receiptPath){WriteAtomic $receiptPath (JsonBytes $result)}
    }
    throw
}finally{foreach($handle in $locks){$handle.Dispose()}}
