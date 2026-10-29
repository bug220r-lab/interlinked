#requires -Version 5.1
<# Guarded hosts-file repair for this specific two-PC direct Ethernet link.
Default is Inspect. -Apply requires elevation and adds only the opposite PC's
known name/address after adapter, manual address, no-gateway, TCP and MAC checks.
No firewall, network-profile, share, client, or project settings are changed.
#>
[CmdletBinding()]
param([switch]$Apply, [switch]$Elevated)

Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'

function Require([bool]$Condition,[string]$Message) { if(-not $Condition){throw $Message} }
function Hash-Bytes([byte[]]$Bytes) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}
    finally{$sha.Dispose()}
}
function Assert-NormalPath([string]$Path,[bool]$Directory) {
    $item=Get-Item -LiteralPath $Path -Force
    Require ($item.PSIsContainer -eq $Directory) ('Unexpected path type: '+$Path)
    $walk=$item
    while($null -ne $walk){
        Require (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) ('Link/reparse path refused: '+$walk.FullName)
        if($walk -is [IO.FileInfo]){$walk=$walk.Directory}else{$walk=$walk.Parent}
    }
}
function Read-ExactBytes([IO.FileStream]$Stream) {
    Require ($Stream.Length -le 1048576) 'Hosts file exceeds the 1 MiB safety limit.'
    $Stream.Position=0
    $bytes=New-Object byte[] ([int]$Stream.Length)
    $done=0
    while($done -lt $bytes.Length){$count=$Stream.Read($bytes,$done,$bytes.Length-$done);Require ($count -gt 0) 'Unexpected end of hosts file.';$done+=$count}
    return ,$bytes
}
function Plan-HostsAppend([byte[]]$Bytes,[string]$PeerName,[string]$PeerAddress) {
    Require ($PeerName -cin @('MSI','THIS-MACHINE')) 'Unexpected peer name.'
    Require ($PeerAddress -cin @('192.168.247.1','192.168.247.2')) 'Unexpected peer address.'
    $encoding='ASCII'
    if($Bytes.Length -ge 3 -and $Bytes[0] -eq 239 -and $Bytes[1] -eq 187 -and $Bytes[2] -eq 191){
        $strict=New-Object Text.UTF8Encoding($false,$true)
        $text=$strict.GetString($Bytes,3,$Bytes.Length-3);$encoding='UTF8-BOM'
    }else{
        foreach($value in $Bytes){Require ($value -le 127 -and $value -ne 0) 'Hosts encoding is ambiguous/unsupported. Only ASCII or explicit UTF-8 BOM is accepted.'}
        $text=[Text.Encoding]::ASCII.GetString($Bytes)
    }
    Require (-not $text.Contains([char]0)) 'Hosts file contains NUL characters.'
    $matchesFound=@()
    foreach($line in ($text -split "`r`n|`n|`r")){
        $content=($line -split '#',2)[0].Trim()
        if(-not $content){continue}
        $parts=@($content -split '\s+')
        if($parts.Count -lt 2){continue}
        foreach($alias in $parts[1..($parts.Count-1)]){
            if($alias.TrimEnd('.') -ieq $PeerName){$matchesFound += $parts[0]}
        }
    }
    Require ($matchesFound.Count -le 1) ('Multiple hosts mappings for '+$PeerName+'; review instead of appending.')
    if($matchesFound.Count -eq 1){Require ($matchesFound[0] -ceq $PeerAddress) ('Conflicting hosts mapping for '+$PeerName+'. Existing content was not changed.')}
    $append=[byte[]]@()
    if($matchesFound.Count -eq 0){
        $newline="`r`n"
        if($text.Contains("`n") -and -not $text.Contains("`r")){$newline="`n"}
        $separator=''
        if($text.Length -gt 0 -and -not ($text.EndsWith("`n") -or $text.EndsWith("`r"))){$separator=$newline}
        $addition=$separator+'# Direct Ethernet peer for MSI Project Library (2026-10-29)'+$newline+$PeerAddress+"`t"+$PeerName+$newline
        $append=[Text.Encoding]::UTF8.GetBytes($addition)
    }
    return [pscustomobject]@{Encoding=$encoding;AlreadyCorrect=($matchesFound.Count -eq 1);AppendBytes=$append}
}
function Test-PeerTcp([string]$Address){
    $client=New-Object Net.Sockets.TcpClient;$wait=$null
    try{$pending=$client.BeginConnect($Address,445,$null,$null);$wait=$pending.AsyncWaitHandle;Require ($wait.WaitOne(5000)) 'Peer TCP 445 timed out.';$client.EndConnect($pending);Require $client.Connected 'Peer SMB port is unavailable.'}
    finally{$client.Dispose();if($null -ne $wait){$wait.Dispose()}}
}
function Save-NewBytes([string]$Path,[byte[]]$Bytes){
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

$computer=[Environment]::MachineName.ToUpperInvariant()
$localRoot=[IO.Path]::GetFullPath($PSScriptRoot)
Require ($localRoot -match '^[A-Za-z]:\\') 'Extract this helper to a local drive before running it.'
Assert-NormalPath $localRoot $true
Require ($computer -cin @('MSI','THIS-MACHINE')) 'This helper is only for MSI and THIS-MACHINE.'
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and -not $admin){
    Require (-not $Elevated) 'The elevated helper did not receive administrator access.'
    Write-Host 'Requesting normal Windows administrator consent for the guarded name repair...'
    $arguments='-NoLogo -NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Apply -Elevated'
    $process=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    Write-Host ('Repair process exit code: '+$process.ExitCode)
    Write-Host ('Receipts/backups: '+(Join-Path $localRoot 'Connection-Repair-Logs'))
    exit $process.ExitCode
}

$mapping=if($computer -ceq 'MSI'){
    @{LocalAddress='192.168.247.1';LocalMac='00-D8-61-E3-D1-DF';PeerAddress='192.168.247.2';PeerMac='30-56-0F-D1-0F-DA';PeerName='THIS-MACHINE'}
}else{
    @{LocalAddress='192.168.247.2';LocalMac='30-56-0F-D1-0F-DA';PeerAddress='192.168.247.1';PeerMac='00-D8-61-E3-D1-DF';PeerName='MSI'}
}
$runId=[Guid]::NewGuid().ToString('N')
$stamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$logRoot=Join-Path $localRoot 'Connection-Repair-Logs'
if(-not (Test-Path -LiteralPath $logRoot)){[void][IO.Directory]::CreateDirectory($logRoot)}
Assert-NormalPath $logRoot $true
$report=[ordered]@{Format='direct-pc-name-repair-v1';RunId=$runId;StartedUtc=[DateTime]::UtcNow.ToString('o');Computer=$computer;ApplyRequested=[bool]$Apply;Status='CHECKING';Mapping=$mapping;HostsChanged=$false;Backup=$null;BeforeSha256=$null;AfterSha256=$null;AclUnchanged=$null;DnsAddresses=@();Errors=@();NoFirewallOrProfileChanges=$true;NoClientSettingsChanges=$true}
$exitCode=2
$hostsPath=Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
try{
    $adapters=@(Get-NetAdapter -IncludeHidden | Where-Object {($_.MacAddress -replace ':','-').ToUpperInvariant() -ceq $mapping.LocalMac})
    Require ($adapters.Count -eq 1) 'The expected direct Ethernet adapter MAC is missing or ambiguous.'
    $adapter=$adapters[0]
    Require ($adapter.Status -eq 'Up' -and $adapter.HardwareInterface) 'The expected hardware Ethernet adapter is not Up.'
    $addresses=@(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4)
    Require ($addresses.Count -eq 1) 'The direct Ethernet adapter has unexpected additional/missing IPv4 addresses.'
    $address=$addresses[0]
    Require ($address.IPAddress -ceq $mapping.LocalAddress -and $address.PrefixLength -eq 24 -and $address.PrefixOrigin -eq 'Manual' -and $address.AddressState -eq 'Preferred') 'The direct Ethernet address is not the expected manual /24 address.'
    $routes=@(Get-NetRoute -InterfaceIndex $adapter.ifIndex | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0')})
    Require ($routes.Count -eq 0) 'A default gateway exists on the direct Ethernet adapter.'
    Test-PeerTcp $mapping.PeerAddress
    $neighbors=@(Get-NetNeighbor -InterfaceIndex $adapter.ifIndex -IPAddress $mapping.PeerAddress -AddressFamily IPv4)
    Require ($neighbors.Count -eq 1) 'Peer neighbor entry is missing or ambiguous.'
    $neighbor=$neighbors[0]
    Require ((($neighbor.LinkLayerAddress -replace ':','-').ToUpperInvariant() -ceq $mapping.PeerMac) -and $neighbor.State -in @('Reachable','Stale','Delay','Probe','Permanent')) 'Peer MAC/state does not match the explicitly verified opposite PC.'
    $report.Interface=[ordered]@{Index=$adapter.ifIndex;Name=$adapter.Name;MacAddress=$adapter.MacAddress;LocalAddress=$address.IPAddress;PrefixLength=$address.PrefixLength;PeerMac=$neighbor.LinkLayerAddress;PeerState=[string]$neighbor.State;PeerTcp445=$true;DefaultGatewayAbsent=$true}
    Assert-NormalPath $hostsPath $false
    $beforeAcl=(Get-Acl -LiteralPath $hostsPath).Sddl
    $mode=if($Apply){[IO.FileAccess]::ReadWrite}else{[IO.FileAccess]::Read}
    $stream=New-Object IO.FileStream($hostsPath,[IO.FileMode]::Open,$mode,[IO.FileShare]::None)
    try{
        $before=Read-ExactBytes $stream
        $plan=Plan-HostsAppend $before $mapping.PeerName $mapping.PeerAddress
        $report.Encoding=$plan.Encoding;$report.AlreadyCorrect=$plan.AlreadyCorrect
        $report.BeforeSha256=Hash-Bytes $before
        $report.BeforeBytes=$before.Length
        $report.BeforeAclSddl=$beforeAcl
        if($Apply -and -not $plan.AlreadyCorrect){
            $backup=Join-Path $logRoot ('hosts-before-'+$computer+'-'+$stamp+'-'+$runId+'.bin')
            Save-NewBytes $backup $before
            Require ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ieq $report.BeforeSha256) 'Backup hash verification failed; hosts was not changed.'
            $report.Backup=$backup
            # A durable intent survives interruption between hosts append and the
            # final receipt, alongside the byte-exact original and its ACL record.
            $intentPath=Join-Path $logRoot ('Hosts-Before-'+$computer+'-'+$stamp+'-'+$runId+'.json')
            $intent=[ordered]@{Format='direct-pc-name-repair-before-v1';RunId=$runId;Computer=$computer;Utc=[DateTime]::UtcNow.ToString('o');HostsPath=$hostsPath;BeforeSha256=$report.BeforeSha256;BeforeBytes=$before.Length;BeforeAclSddl=$beforeAcl;Backup=$backup;Mapping=$mapping;AppendSha256=(Hash-Bytes $plan.AppendBytes);AppendBytes=$plan.AppendBytes.Length;Status='BEFORE_APPEND'}
            Save-NewBytes $intentPath ([Text.Encoding]::UTF8.GetBytes(($intent | ConvertTo-Json -Depth 5)))
            $report.BeforeReceipt=$intentPath
            $expected=New-Object byte[] ($before.Length+$plan.AppendBytes.Length)
            [Array]::Copy($before,0,$expected,0,$before.Length)
            [Array]::Copy($plan.AppendBytes,0,$expected,$before.Length,$plan.AppendBytes.Length)
            try{
                $stream.Position=$stream.Length
                $stream.Write($plan.AppendBytes,0,$plan.AppendBytes.Length);$stream.Flush($true)
                $after=Read-ExactBytes $stream
                Require ((Hash-Bytes $after) -ceq (Hash-Bytes $expected)) 'Hosts append readback differs from the exact original bytes plus new mapping.'
                $report.HostsChanged=$true
            }catch{
                $appendFailure=$_.Exception.Message
                try{$stream.SetLength($before.Length);$stream.Flush($true);Require ((Hash-Bytes (Read-ExactBytes $stream)) -ceq $report.BeforeSha256) 'Rollback hash mismatch.';$report.AppendRollback='ORIGINAL_BYTES_RESTORED'}
                catch{$report.AppendRollback='FAILED: '+$_.Exception.Message}
                throw $appendFailure
            }
        }
        $report.AfterSha256=Hash-Bytes (Read-ExactBytes $stream)
        $report.AfterBytes=$stream.Length
    }finally{$stream.Dispose()}
    $report.AclUnchanged=((Get-Acl -LiteralPath $hostsPath).Sddl -ceq $beforeAcl)
    Require $report.AclUnchanged 'The hosts ACL changed unexpectedly; inspect the backup/receipt before continuing.'
    if($Apply){
        Clear-DnsClientCache
        $resolved=@([Net.Dns]::GetHostAddresses($mapping.PeerName) | ForEach-Object {$_.IPAddressToString} | Sort-Object -Unique)
        $report.DnsAddresses=$resolved
        Require ($resolved.Count -eq 1 -and $resolved[0] -ceq $mapping.PeerAddress) 'The peer name did not resolve exclusively to the expected direct Ethernet address after DNS cache clear.'
        $report.Status=if($report.HostsChanged){'APPLIED_AND_DNS_VERIFIED'}else{'ALREADY_CORRECT_AND_DNS_VERIFIED'}
    }else{$report.Status='INSPECT_PASSED_NO_CHANGE'}
    $exitCode=0
}catch{
    $report.Errors += $_.Exception.Message
    $report.Status=if($report.HostsChanged){'APPLIED_POSTCHECK_FAILED'}else{'BLOCKED_NO_SUCCESS_CLAIM'}
}
$report.CompletedUtc=[DateTime]::UtcNow.ToString('o')
$reportPath=Join-Path $logRoot ('Hosts-Repair-'+$computer+'-'+$stamp+'-'+$runId+'.json')
try{Save-NewBytes $reportPath ([Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 8)))}
catch{Write-Warning ('Could not save repair receipt: '+$_.Exception.Message);$exitCode=2}
Write-Host ('Result: '+$report.Status)
foreach($message in $report.Errors){Write-Host $message}
Write-Host ('Receipt: '+$reportPath)
exit $exitCode
