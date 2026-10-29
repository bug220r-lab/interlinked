#Requires -RunAsAdministrator
$ErrorActionPreference='Stop'
if($env:COMPUTERNAME -ne 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-21-3086848077-1833151167-136462239-1001'){throw 'Wrong host/account.'}
$adapter=Get-NetAdapter -Name 'Ethernet'
if($adapter.Status -ne 'Up'){throw 'The direct Ethernet cable is not connected.'}
$ip=@(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq '192.168.247.1' -and $_.PrefixLength -eq 24})
if($ip.Count -ne 1){throw 'The expected direct-link address changed.'}
if(@(Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).Count){throw 'This adapter has a default gateway; refusing to treat it as the observed direct cable.'}
$profiles=@(Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex)
if($profiles.Count -ne 1 -or $profiles[0].NetworkCategory.ToString() -notin @('Public','Private')){throw 'Unexpected Ethernet network profile.'}
$receipt=Join-Path $PSScriptRoot 'direct-ethernet-profile.json'
if(Test-Path -LiteralPath $receipt){throw 'Existing profile receipt found; inspect instead of changing again.'}
$record=[ordered]@{Computer='MSI';InterfaceAlias='Ethernet';InterfaceIndex=$adapter.ifIndex;InterfaceGuid=[string]$adapter.InterfaceGuid;LocalIP='192.168.247.1';PeerIP='192.168.247.2';OldCategory=[string]$profiles[0].NetworkCategory;ChangedUtc=[DateTime]::UtcNow.ToString('o');Status='CHANGE_REQUESTED'}
$record|ConvertTo-Json|Set-Content -LiteralPath $receipt -Encoding UTF8
if($profiles[0].NetworkCategory.ToString() -eq 'Public'){Set-NetConnectionProfile -InterfaceIndex $adapter.ifIndex -NetworkCategory Private}
$now=Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex
if($now.NetworkCategory.ToString() -ne 'Private'){throw 'Private profile readback failed.'}
$record.Status='PRIVATE_READBACK_VERIFIED';$record.NewCategory=[string]$now.NetworkCategory
$record|ConvertTo-Json|Set-Content -LiteralPath $receipt -Encoding UTF8
