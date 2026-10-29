#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([ValidateRange(1,60)][int]$Attempts=60)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$ExpectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$ExpectedGuid=[guid]'2A683B7E-1B94-4858-97C4-48823A0F2AAB'
$ProtectedRoot='C:\ProgramData\MSI-Coding-Direct-Ethernet-20261029'
$PeerIP='192.168.247.2'
$PeerMac='30-56-0F-D1-0F-DA'

function Assert-PlainPath([string]$Path){
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){
            if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Reparse path refused: $p"}
        }
        $parent=[IO.Directory]::GetParent($p)
        if($null -eq $parent){break}; $p=$parent.FullName
    }
}
function Write-NewJson([string]$Path,$Value){
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 10))
    $s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}
}
function Get-DirectLink {
    $a=@(Get-NetAdapter | Where-Object {[guid]$_.InterfaceGuid -eq $ExpectedGuid})
    if($a.Count -ne 1 -or $a[0].Name -ne 'Ethernet'){throw 'Pinned Ethernet adapter is absent or renamed.'}
    $a=$a[0]
    if($a.Status -ne 'Up'){return [pscustomobject]@{Ready=$false;Reason='Cable is not Up'}}
    $ips=@(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4)
    if($ips.Count -ne 1 -or $ips[0].IPAddress -ne '192.168.247.1' -or $ips[0].PrefixLength -ne 24 -or [string]$ips[0].PrefixOrigin -ne 'Manual' -or [string]$ips[0].AddressState -ne 'Preferred'){
        throw 'Exact manual IPv4 192.168.247.1/24 is not the sole preferred IPv4 address.'
    }
    $ipif=Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4
    if([string]$ipif.Dhcp -ne 'Disabled'){throw 'The pinned direct link no longer has DHCP disabled.'}
    $routes=@(Get-NetRoute -InterfaceIndex $a.ifIndex | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0') -or $_.NextHop -notin @('0.0.0.0','::')})
    if($routes.Count){throw 'A gateway or routed next hop exists on the pinned adapter.'}
    $p=@(Get-NetConnectionProfile -InterfaceIndex $a.ifIndex)
    if($p.Count -eq 0){return [pscustomobject]@{Ready=$false;Reason='Windows has not created the connection profile'}}
    if($p.Count -ne 1 -or [string]$p[0].NetworkCategory -notin @('Public','Private')){throw 'Unexpected or domain-authenticated profile.'}
    return [pscustomobject]@{Ready=$true;Adapter=$a;Profile=$p[0]}
}
function Test-ExpectedPeer([int]$Index){
    # A successful reply plus the pinned L2 neighbor is an additional cable check,
    # not cryptographic authentication. No hostname/DNS or Internet access is used.
    $ping=New-Object Net.NetworkInformation.Ping
    try{$reply=$ping.Send($PeerIP,750)}finally{$ping.Dispose()}
    if($reply.Status -ne [Net.NetworkInformation.IPStatus]::Success){return $false}
    $n=@(Get-NetNeighbor -InterfaceIndex $Index -AddressFamily IPv4 -IPAddress $PeerIP -ErrorAction SilentlyContinue)
    return ($n.Count -eq 1 -and $n[0].LinkLayerAddress.ToUpperInvariant() -eq $PeerMac -and [string]$n[0].State -in @('Reachable','Stale','Delay','Probe','Permanent'))
}

if($env:COMPUTERNAME -ne 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $ExpectedSid){throw 'Wrong host/account.'}
if([IO.Path]::GetFullPath($PSScriptRoot) -ne $ProtectedRoot){throw 'Run only the installed administrator-protected worker.'}
Assert-PlainPath $PSCommandPath
Assert-PlainPath (Join-Path $ProtectedRoot 'OWNER.json')
$owner=Get-Content -LiteralPath (Join-Path $ProtectedRoot 'OWNER.json') -Raw|ConvertFrom-Json
if($owner.Format -ne 'msi-direct-ethernet-v1' -or $owner.Status -ne 'INSTALLED' -or $owner.Sid -ne $ExpectedSid -or $owner.WorkerSha256 -ne (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash){throw 'Installed worker ownership/hash does not match.'}
Assert-PlainPath (Join-Path $ProtectedRoot 'Receipts')
Assert-PlainPath (Join-Path $ProtectedRoot '.run.lock')
$lock=[IO.File]::Open((Join-Path $ProtectedRoot '.run.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$r=[ordered]@{Format='msi-direct-ethernet-run-v1';SetupId=$owner.SetupId;StartedUtc=[DateTime]::UtcNow.ToString('o');Computer='MSI';Sid=$ExpectedSid;InterfaceGuid=[string]$ExpectedGuid;LocalIP='192.168.247.1';PeerIP=$PeerIP;PeerMac=$PeerMac;Status='STARTED';Attempts=0;Changed=$false;OldCategory=$null;NewCategory=$null;Reason=$null}
$exitCode=1
try{
    for($i=1;$i -le $Attempts;$i++){
        $r.Attempts=$i
        $link=Get-DirectLink
        if($link.Ready -and (Test-ExpectedPeer $link.Adapter.ifIndex)){
            # Re-read every local guard after the peer probe, immediately before change.
            $link=Get-DirectLink
            if(-not $link.Ready){throw 'The cable/profile changed during the peer check.'}
            $r.OldCategory=[string]$link.Profile.NetworkCategory
            if($r.OldCategory -eq 'Public'){
                Set-NetConnectionProfile -InputObject $link.Profile -NetworkCategory Private
                $r.Changed=$true
            }
            $after=Get-DirectLink
            if(-not $after.Ready -or [string]$after.Profile.NetworkCategory -ne 'Private'){throw 'Private profile readback failed.'}
            $r.NewCategory=[string]$after.Profile.NetworkCategory
            $r.Status='PRIVATE_READBACK_VERIFIED';$r.Reason=$null;$exitCode=0;break
        }
        $r.Reason=if($link.Ready){'Expected peer did not reply with the pinned MAC'}else{$link.Reason}
        if($i -lt $Attempts){Start-Sleep -Seconds 2}
    }
    if($exitCode -ne 0){$r.Status='NO_CHANGE_RETRY_WINDOW_EXPIRED'}
}catch{$r.Status='GUARD_OR_OPERATION_FAILED';$r.Reason=$_.Exception.Message}
finally{
    $r.FinishedUtc=[DateTime]::UtcNow.ToString('o')
    try{Write-NewJson (Join-Path $ProtectedRoot ('Receipts\Run-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')+'-'+[guid]::NewGuid().ToString('N')+'.json')) $r}finally{$lock.Dispose()}
}
$r|ConvertTo-Json -Depth 6
exit $exitCode
