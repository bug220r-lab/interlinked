#Requires -Version 5.1
#Requires -RunAsAdministrator
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$sid='S-1-5-21-3086848077-1833151167-136462239-1001'
$guid=[guid]'2A683B7E-1B94-4858-97C4-48823A0F2AAB'
function Read-ExactCable {
    $a=@(Get-NetAdapter|Where-Object {[guid]$_.InterfaceGuid -eq $guid})
    if($a.Count -ne 1 -or $a[0].Name -ne 'Ethernet' -or $a[0].Status -ne 'Up'){throw 'Expected Ethernet cable is not Up.'}
    $a=$a[0]
    $ip=@(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4)
    if($ip.Count -ne 1 -or $ip[0].IPAddress -ne '192.168.247.1' -or $ip[0].PrefixLength -ne 24 -or [string]$ip[0].PrefixOrigin -ne 'Manual' -or [string]$ip[0].AddressState -ne 'Preferred'){throw 'The exact static .1/24 address is no longer the sole preferred IPv4 address.'}
    if([string](Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4).Dhcp -ne 'Disabled'){throw 'DHCP is no longer disabled.'}
    if(@(Get-NetRoute -InterfaceIndex $a.ifIndex|Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0') -or $_.NextHop -notin @('0.0.0.0','::')}).Count){throw 'A gateway or routed next hop exists on Ethernet.'}
    $p=@(Get-NetConnectionProfile -InterfaceIndex $a.ifIndex)
    if($p.Count -ne 1 -or [string]$p[0].NetworkCategory -notin @('Public','Private')){throw 'Unexpected Ethernet connection profile.'}
    [pscustomobject]@{Adapter=$a;Profile=$p[0]}
}
function New-Receipt([string]$Path,$Value){
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 6))
    $s=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}
}
if($env:COMPUTERNAME -ne 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $sid){throw 'Wrong host/account.'}
$original=Join-Path $PSScriptRoot 'direct-ethernet-profile.json'
$old=Get-Content -LiteralPath $original -Raw|ConvertFrom-Json
if($old.Status -ne 'PRIVATE_READBACK_VERIFIED' -or [guid]$old.InterfaceGuid -ne $guid -or $old.LocalIP -ne '192.168.247.1' -or $old.PeerIP -ne '192.168.247.2'){throw 'Original observed-cable receipt mismatch.'}
$link=Read-ExactCable
$tcp=New-Object Net.Sockets.TcpClient
$pending=$null
try{$pending=$tcp.BeginConnect('192.168.247.2',445,$null,$null);if(-not $pending.AsyncWaitHandle.WaitOne(1500)){throw 'The expected new PC did not answer TCP445.'};$tcp.EndConnect($pending)}finally{if($null -ne $pending){$pending.AsyncWaitHandle.Close()};$tcp.Close()}
$peer=@(Get-NetNeighbor -InterfaceIndex $link.Adapter.ifIndex -AddressFamily IPv4 -IPAddress '192.168.247.2')
if($peer.Count -ne 1 -or $peer[0].LinkLayerAddress -ne '30-56-0F-D1-0F-DA' -or [string]$peer[0].State -notin @('Reachable','Stale','Delay','Probe','Permanent')){throw 'Observed new-PC Ethernet MAC does not match.'}
$link=Read-ExactCable
$id=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')+'-'+[guid]::NewGuid().ToString('N')
$r=[ordered]@{Format='msi-direct-ethernet-now-v1';Computer='MSI';Sid=$sid;StartedUtc=[DateTime]::UtcNow.ToString('o');InterfaceGuid=[string]$guid;LocalIP='192.168.247.1';PeerIP='192.168.247.2';PeerMac='30-56-0F-D1-0F-DA';OriginalReceiptSha256=(Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash;OldCategory=[string]$link.Profile.NetworkCategory;NewCategory=$null;Status='CHANGE_REQUESTED';Failure=$null}
New-Receipt (Join-Path $PSScriptRoot ('direct-ethernet-'+$id+'-before.json')) $r
try{
    if($r.OldCategory -eq 'Public'){Set-NetConnectionProfile -InputObject $link.Profile -NetworkCategory Private}
    $after=Read-ExactCable
    if([string]$after.Profile.NetworkCategory -ne 'Private'){throw 'Private category readback failed.'}
    $r.NewCategory='Private';$r.Status='PRIVATE_READBACK_VERIFIED'
}catch{$r.Status='FAILED_REVIEW_REQUIRED';$r.Failure=$_.Exception.Message;throw}
finally{New-Receipt (Join-Path $PSScriptRoot ('direct-ethernet-'+$id+'-result.json')) $r}
$r|ConvertTo-Json -Depth 6
