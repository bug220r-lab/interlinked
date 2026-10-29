#requires -Version 5.1
<# Run locally on THIS-MACHINE, as the normal signed-in user.
Reads the catalog header and creates one unique, temporary Returns test file.
Does not change settings, run projects, sign in to SSH, or change credentials.
#>
[CmdletBinding()]
param([ValidateRange(1000,10000)][int]$TimeoutMilliseconds = 5000)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$canonicalServer = 'MSI'
$directAddress = '192.168.247.1'
$libraryRoot = '\\MSI\ProjectLibrary'
$returnsRoot = '\\MSI\ProjectReturns'
$runId = [Guid]::NewGuid().ToString('N')
$result = [ordered]@{
    Format = 'msi-new-pc-connection-check-v1'
    RunId = $runId
    StartedUtc = [DateTime]::UtcNow.ToString('o')
    Computer = [Environment]::MachineName
    ExpectedComputer = 'THIS-MACHINE'
    Status = 'CHECKING'
    CanonicalServer = $canonicalServer
    DirectAddress = $directAddress
    Tcp = @()
    Catalog = $null
    Returns = $null
    Errors = @()
    Scope = 'Connectivity and isolated tiny-file check only; no SSH authentication, project execution, or full catalog/payload verification.'
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-ByteHash([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Assert-LocalKitDirectory([string]$Directory) {
    $full = [IO.Path]::GetFullPath($Directory)
    Assert-Condition ($full -match '^[A-Za-z]:\\') 'Extract this kit to a local drive and run it there, not from a ZIP or network share.'
    $walk = Get-Item -LiteralPath $full -Force
    while ($null -ne $walk) {
        Assert-Condition (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'The kit directory or an ancestor is a link/reparse point; use a normal local folder.'
        $walk = $walk.Parent
    }
    return $full
}

function Test-BoundedTcp([string]$ServerName, [int]$Port, [int]$Timeout) {
    $client = New-Object Net.Sockets.TcpClient
    $waitHandle = $null
    $probe = [ordered]@{Server=$ServerName; Port=$Port; Connected=$false; RemoteEndpoint=$null; ElapsedMilliseconds=0; Error=$null}
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $pending = $client.BeginConnect($ServerName, $Port, $null, $null)
        $waitHandle = $pending.AsyncWaitHandle
        if (-not $waitHandle.WaitOne($Timeout)) { throw ('TCP connection timed out after {0} ms.' -f $Timeout) }
        $client.EndConnect($pending)
        $probe.Connected = $client.Connected
        $probe.RemoteEndpoint = [string]$client.Client.RemoteEndPoint
    }
    catch { $probe.Error = $_.Exception.Message }
    finally {
        $client.Dispose()
        if ($null -ne $waitHandle) { $waitHandle.Dispose() }
        $watch.Stop()
        $probe.ElapsedMilliseconds = $watch.ElapsedMilliseconds
    }
    return [pscustomobject]$probe
}

function Test-CatalogHeader([string]$Root) {
    $path = [IO.Path]::Combine($Root, 'catalog.json')
    $item = Get-Item -LiteralPath $path -Force
    Assert-Condition (-not $item.PSIsContainer) 'The library catalog is not a regular file.'
    Assert-Condition (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'The library catalog is a link/reparse point.'
    Assert-Condition ($item.Length -gt 0) 'The library catalog is empty.'
    $stream = New-Object IO.FileStream($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $bytes = New-Object byte[] ([Math]::Min(65536, $stream.Length))
        $read = 0
        while ($read -lt $bytes.Length) {
            $count = $stream.Read($bytes, $read, $bytes.Length - $read)
            Assert-Condition ($count -gt 0) 'Unexpected end of catalog header.'
            $read += $count
        }
        $header = [Text.Encoding]::UTF8.GetString($bytes)
        Assert-Condition ($header -match '"format"\s*:\s*"project-library-catalog-v1"') 'Catalog format is not recognized in its header.'
        $catalogMatch = [regex]::Match($header, '"catalogId"\s*:\s*"([A-Za-z0-9._-]+)"')
        Assert-Condition ($catalogMatch.Success) 'The catalog identity is missing or unrecognized.'
        return [pscustomobject]@{Status='HEADER_READ'; Path=$path; CatalogId=$catalogMatch.Groups[1].Value; CatalogBytes=$stream.Length; BytesRead=$read; HeaderSha256=(Get-ByteHash $bytes); FullCatalogValidated=$false}
    }
    finally { $stream.Dispose() }
}

function Test-ReturnsScratch([string]$Root, [string]$Identifier) {
    # Fixed share root + random direct child; never enumerate, replace, or remove projects.
    Assert-Condition ($Root -ceq '\\MSI\ProjectReturns') 'Unexpected Returns share.'
    Assert-Condition ($Identifier -match '^[a-f0-9]{32}$') 'Invalid check identifier.'
    $rootItem = Get-Item -LiteralPath $Root -Force
    Assert-Condition ($rootItem.PSIsContainer) 'Returns share is not a directory.'
    Assert-Condition (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Returns share root is a link/reparse point.'
    $name = 'Connection-Check-THIS-MACHINE-' + $Identifier + '.tmp'
    $target = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, $name))
    Assert-Condition ([IO.Path]::GetDirectoryName($target).TrimEnd('\') -ceq $Root.TrimEnd('\')) 'Scratch target escapes the exact Returns root.'
    Assert-Condition ([IO.Path]::GetFileName($target) -ceq $name) 'Unexpected scratch filename.'
    $payload = [Text.Encoding]::UTF8.GetBytes("MSI connection check only. No project data.`r`nRun: $Identifier`r`n")
    $stream = $null
    $writeVerified = $false
    $cleanupVerified = $false
    try {
        # CreateNew rejects any preexisting path. DeleteOnClose removes only this
        # opened file, including if the checker process is interrupted.
        $stream = New-Object IO.FileStream($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose)
        $stream.Write($payload, 0, $payload.Length)
        $stream.Flush($true)
        $stream.Position = 0
        $readback = New-Object byte[] $payload.Length
        $read = 0
        while ($read -lt $readback.Length) {
            $count = $stream.Read($readback, $read, $readback.Length - $read)
            Assert-Condition ($count -gt 0) 'Unexpected end of scratch readback.'
            $read += $count
        }
        Assert-Condition ($stream.Length -eq $payload.Length) 'Scratch length differs from the written bytes.'
        $expectedHash = Get-ByteHash $payload
        $actualHash = Get-ByteHash $readback
        Assert-Condition ($actualHash -ceq $expectedHash) 'Scratch readback SHA256 differs.'
        $writeVerified = $true
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
    # Check exact owned path. Never issue a broad deletion if cleanup is delayed.
    $cleanupVerified = -not (Test-Path -LiteralPath $target -ErrorAction Stop)
    Assert-Condition $cleanupVerified ('Temporary check file is still present; no other deletion was attempted: ' + $target)
    return [pscustomobject]@{Status='WRITE_READ_HASH_DELETE_VERIFIED'; Path=$target; Bytes=$payload.Length; Sha256=$actualHash; ReadbackVerified=$writeVerified; TemporaryFileRemoved=$cleanupVerified}
}

$exitCode = 2
$localKit = $null
try {
    $localKit = Assert-LocalKitDirectory $PSScriptRoot
    Assert-Condition ([Environment]::MachineName -ieq 'THIS-MACHINE') 'Run this checker on the NEW PC named THIS-MACHINE, not on MSI.'
    Write-Host 'Checking MSI name and direct Ethernet address (bounded TCP probes)...'
    foreach ($serverName in @($canonicalServer, $directAddress)) {
        foreach ($port in @(445, 22)) {
            $probe = Test-BoundedTcp $serverName $port $TimeoutMilliseconds
            $result.Tcp += $probe
            Write-Host ('  {0}:{1}  {2}' -f $serverName, $port, $(if ($probe.Connected) {'CONNECTED'} else {'UNAVAILABLE'}))
        }
    }
    $canonicalSmb = @($result.Tcp | Where-Object { $_.Server -ceq $canonicalServer -and $_.Port -eq 445 })[0]
    if ($canonicalSmb.Connected) {
        Write-Host 'Reading the canonical library catalog header...'
        try { $result.Catalog = Test-CatalogHeader $libraryRoot }
        catch { $result.Errors += ('Catalog: ' + $_.Exception.Message) }
        Write-Host 'Checking one isolated temporary file in canonical ProjectReturns...'
        try { $result.Returns = Test-ReturnsScratch $returnsRoot $runId }
        catch { $result.Errors += ('Returns: ' + $_.Exception.Message) }
    }
    else {
        $directSmb = @($result.Tcp | Where-Object { $_.Server -ceq $directAddress -and $_.Port -eq 445 })[0]
        if ($directSmb.Connected) { $result.Errors += 'MSI name TCP 445 failed while 192.168.247.1 TCP 445 succeeded. Investigate MSI name resolution/routing; keep canonical MSI client settings and ownership bindings unchanged.' }
        else { $result.Errors += 'Canonical MSI SMB and direct Ethernet SMB were unavailable. Check power, cable, Private network profile, and allowed sharing scope.' }
    }
    $fileReady = $null -ne $result.Catalog -and $null -ne $result.Returns
    $sshReady = @($result.Tcp | Where-Object { $_.Port -eq 22 -and $_.Connected }).Count -eq 2
    if ($fileReady -and $sshReady) { $result.Status='FILE_WORKFLOW_AND_SSH_TCP_REACHABLE'; $exitCode=0 }
    elseif ($fileReady) { $result.Status='FILE_WORKFLOW_REACHABLE_SSH_TCP_INCOMPLETE'; $exitCode=1 }
    else { $result.Status='CONNECTION_CHECK_INCOMPLETE' }
}
catch { $result.Status='BLOCKED'; $result.Errors += $_.Exception.Message }

$result.CompletedUtc = [DateTime]::UtcNow.ToString('o')
$result.SshAuthenticationVerified = $false
$result.SettingsChanged = $false
$resultPath = $null
if ($null -ne $localKit) {
    try {
        $resultPath = [IO.Path]::Combine($localKit, 'MSI-Connection-Check-' + $runId + '.json')
        $bytes = [Text.Encoding]::UTF8.GetBytes(($result | ConvertTo-Json -Depth 8))
        $reportStream = New-Object IO.FileStream($resultPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $reportStream.Write($bytes, 0, $bytes.Length); $reportStream.Flush($true) }
        finally { $reportStream.Dispose() }
    }
    catch { Write-Warning ('Could not save the local check report: ' + $_.Exception.Message); $exitCode=2 }
}
Write-Host ('Result: ' + $result.Status)
foreach ($message in $result.Errors) { Write-Host ('  ' + $message) }
if ($null -ne $resultPath) { Write-Host ('Report: ' + $resultPath) }
Write-Host 'TCP 22 reachability does not verify SSH sign-in. No settings or passwords were changed.'
exit $exitCode
