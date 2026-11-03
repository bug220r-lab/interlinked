[CmdletBinding(SupportsShouldProcess=$true)]
param([Parameter(Mandatory=$true)][string]$InstallDirectory,[Parameter(Mandatory=$true)][string]$ClientAddress,[switch]$Disable)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($InstallDirectory)
$record=Get-Content -LiteralPath (Join-Path $root 'node.json') -Raw | ConvertFrom-Json
if($record.computer -ine $env:COMPUTERNAME -or $record.sid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value){throw 'Node identity differs.'}
if($record.bind -eq '127.0.0.1'){throw 'This node is configured for loopback only.'}
$parsed=$null
if(-not [Net.IPAddress]::TryParse($ClientAddress,[ref]$parsed) -or $ClientAddress -eq '0.0.0.0'){throw 'Choose the exact paired client interface address.'}
$name='INTERLINKED-AI-Node-'+$record.sid
$rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if($rule){
    $port=$rule | Get-NetFirewallPortFilter
    $address=$rule | Get-NetFirewallAddressFilter
    $program=$rule | Get-NetFirewallApplicationFilter
    if($rule.Description -cne ('Owned by '+$root) -or $port.LocalPort -ne $record.port -or $program.Program -ine $record.python -or $address.LocalAddress -ne $record.bind -or $address.RemoteAddress -ne $ClientAddress){throw 'Differing firewall rule is preserved.'}
    if($Disable -and $PSCmdlet.ShouldProcess($name,'Disable owned AI node network access')){Disable-NetFirewallRule -Name $name | Out-Null}
    elseif(-not $Disable -and $PSCmdlet.ShouldProcess($name,'Enable owned AI node network access')){Enable-NetFirewallRule -Name $name | Out-Null}
} elseif(-not $Disable -and $PSCmdlet.ShouldProcess($name,'Create narrowly scoped AI node network rule')){
    New-NetFirewallRule -Name $name -DisplayName 'INTERLINKED AI Node' -Description ('Owned by '+$root) -Direction Inbound -Action Allow -Protocol TCP -LocalPort $record.port -LocalAddress $record.bind -RemoteAddress $ClientAddress -Program $record.python -Profile Private | Out-Null
}
