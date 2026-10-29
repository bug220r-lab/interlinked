#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$RunnerSha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$CommandSha256
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$fixture = 'Verification-New-PC-c8c95fcd7a3a44a2aa2c04843b68e187'
$workspace = 'C:\Users\jlewi\Documents\Codex\2026-10-28\prep'
$staging = Join-Path $PSScriptRoot ('Prepared\'+$fixture)
$output = Join-Path $workspace 'outputs\Project-Library-2026-10-29'
$library = Join-Path $output ('HostLibrary\'+$fixture)
$returns = Join-Path $output ('Returns\'+$fixture)
$kit = '\\192.168.247.2\Users\Jude\Documents\MSI-Project-Library'
$receiptPath = Join-Path $PSScriptRoot ('publication-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N')+'.json')
$receipt = [ordered]@{Status='PREFLIGHT';StartedUtc=[DateTime]::UtcNow.ToString('o');Fixture=$fixture;Library=$library;Returns=$returns;NewPcKit=$kit;Files=@();Error=$null}
function Require([bool]$Condition,[string]$Message) { if(-not $Condition){throw $Message} }
function Plain([string]$Path) {
    $walk = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while($null -ne $walk){
        Require (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) ('Reparse path refused: '+$walk.FullName)
        if($walk -is [IO.DirectoryInfo]){$walk=$walk.Parent}else{$walk=$walk.Directory}
    }
}
function Hash([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Copy-NewVerified($Row){
    Plain $Row.Source
    Require ((Hash $Row.Source) -ceq $Row.Sha256) ('Source changed: '+$Row.Source)
    $parent = [IO.Path]::GetDirectoryName($Row.Destination)
    if(-not (Test-Path -LiteralPath $parent)){[IO.Directory]::CreateDirectory($parent)|Out-Null}
    Plain $parent
    $bytes=[IO.File]::ReadAllBytes($Row.Source)
    $stream=New-Object IO.FileStream($Row.Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    [IO.File]::SetLastWriteTimeUtc($Row.Destination,[IO.File]::GetLastWriteTimeUtc($Row.Source))
    Require ((Hash $Row.Destination) -ceq $Row.Sha256) ('Destination hash differs: '+$Row.Destination)
    $receipt.Files += [pscustomobject]@{Source=$Row.Source;Destination=$Row.Destination;Bytes=$bytes.Length;Sha256=$Row.Sha256;Verified=$true}
}
try{
    Require ($env:COMPUTERNAME -ieq 'MSI') 'Publish only from MSI.'
    Plain $staging;Plain $kit;Plain (Join-Path $output 'HostLibrary');Plain (Join-Path $output 'Returns')
    Require (-not (Test-Path -LiteralPath $library)) 'Fixture library destination exists; refusing reuse.'
    Require (-not (Test-Path -LiteralPath $returns)) 'Fixture Returns destination exists; refusing reuse.'
    $manifest=Get-Content -LiteralPath (Join-Path $staging 'STAGING-RECEIPT.json') -Raw|ConvertFrom-Json
    $plan=Get-Content -LiteralPath (Join-Path $staging 'New-PC-Verification-Plan.json') -Raw|ConvertFrom-Json
    Require ($manifest.fixtureName -ceq $fixture -and $plan.fixtureName -ceq $fixture -and @($manifest.files).Count -eq 12) 'Unexpected staged fixture.'
    $rows=@()
    $destinations=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach($item in $manifest.files){
        $relative=[string]$item.path
        Require ($relative -match '^(Library|Returns)/' -and $relative -notmatch '[:\\]' -and @($relative.Split('/')|Where-Object {$_ -in @('','.','..')}).Count -eq 0) 'Unsafe staged relative path.'
        $parts=$relative.Split('/',2)
        $base=if($parts[0] -ceq 'Library'){$library}else{$returns}
        $destination=[IO.Path]::GetFullPath((Join-Path $base $parts[1]))
        Require ($destination.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)) 'Fixture path escapes its exact directory.'
        Require ($destinations.Add($destination)) 'Duplicate staged destination.'
        $source=Join-Path $staging $relative
        Plain $source
        Require ((Hash $source) -ceq $item.sha256 -and (Get-Item -LiteralPath $source).Length -eq $item.bytes) 'Staged bytes differ.'
        $rows += [pscustomobject]@{Source=$source;Destination=$destination;Sha256=$item.sha256}
    }
    foreach($pair in @(
        @{Name='verify_new_pc_workflow.py';Source=(Join-Path $PSScriptRoot 'verify_new_pc_workflow.py');Sha256=$RunnerSha256},
        @{Name='Verify-New-PC-Workflow.cmd';Source=(Join-Path $PSScriptRoot 'Verify-New-PC-Workflow.cmd');Sha256=$CommandSha256},
        @{Name='New-PC-Verification-Plan.json';Source=(Join-Path $staging 'New-PC-Verification-Plan.json');Sha256=(Hash (Join-Path $staging 'New-PC-Verification-Plan.json'))}
    )){
        $destination=Join-Path $kit $pair.Name
        Require (-not (Test-Path -LiteralPath $destination)) ('Helper already exists; preserve it: '+$destination)
        Plain $pair.Source
        Require ((Hash $pair.Source) -ceq $pair.Sha256) ('Reviewed helper changed: '+$pair.Name)
        $rows += [pscustomobject]@{Source=$pair.Source;Destination=$destination;Sha256=$pair.Sha256}
    }
    $receipt.Status='PUBLISHING'
    New-Item -ItemType Directory -Path $library -ErrorAction Stop|Out-Null
    New-Item -ItemType Directory -Path $returns -ErrorAction Stop|Out-Null
    foreach($row in $rows){Copy-NewVerified $row}
    $receipt.Status='PUBLISHED_AND_15_FILES_SHA256_VERIFIED'
}catch{$receipt.Status='INCOMPLETE_REVIEW_REQUIRED';$receipt.Error=$_.Exception.Message}
$receipt.FinishedUtc=[DateTime]::UtcNow.ToString('o')
[IO.File]::WriteAllText($receiptPath,($receipt|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
[pscustomobject]@{Status=$receipt.Status;VerifiedFiles=$receipt.Files.Count;Receipt=$receiptPath;Error=$receipt.Error}|ConvertTo-Json
if($receipt.Status -ne 'PUBLISHED_AND_15_FILES_SHA256_VERIFIED'){exit 1}
