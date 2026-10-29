#requires -Version 5.1
<# Read only the exact Ed25519 PUBLIC key and saved verification/state records.
No private key, connection, installation, service, firewall or settings actions.
The only write is one CreateNew public-key receipt in this workspace. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$base='C:\Users\jlewi\Documents\Codex\2026-10-28\prep'
$keyPath='C:\ProgramData\ssh\ssh_host_ed25519_key.pub'
$verifyPath=Join-Path $base 'outputs\Project-Library-2026-10-29\Server-Setup\Logs\VERIFY-20261029T220537-6cda1ff9.json'
$statePath=Join-Path $base 'outputs\Project-Library-2026-10-29\Server-Setup\State\SETUP-STATE.json'
$expectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
$expectedFingerprint='SHA256:oJliv6DyXoZZERIXw7FSD7ekI/PqxSLU5YPv5d5ApiA'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if([Environment]::MachineName -ine 'MSI' -or $identity.User.Value -cne $expectedSid){throw 'Unexpected host/account; no public key read.'}
function Hash([byte[]]$bytes){$h=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($h.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}}
function Plain([string]$path){$item=Get-Item -LiteralPath $path -Force;while($null -ne $item){if(($item.Attributes -band 0x400) -ne 0){throw 'Reparse source/control path refused.'};$item=if($item -is [IO.FileInfo]){$item.Directory}else{$item.Parent}}}
function Read-Held([string]$path,[int]$maximum){
    Plain $path
    $s=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        if($s.Length -gt $maximum){throw 'Unexpectedly large public/control file.'}
        $m=New-Object IO.MemoryStream
        try{$s.CopyTo($m);return ,$m.ToArray()}finally{$m.Dispose()}
    }finally{$s.Dispose()}
}
$verifyBytes=Read-Held $verifyPath 1048576
if((Hash $verifyBytes) -cne '5eeda3197e1a8a944a3ed32cba58623873b4d97026f46506da9d066cdbd9ccc9'){throw 'Reviewed server verification receipt changed.'}
$verify=[Text.Encoding]::UTF8.GetString($verifyBytes).TrimStart([char]0xFEFF)|ConvertFrom-Json
if($verify.Format -cne 'msi-coding-server-verification-v1' -or $verify.Status -cne 'LOCAL_SERVER_VERIFIED_CLIENT_LOGIN_PENDING' -or $verify.ClientLoginTested -ne $false){throw 'Unexpected server verification state.'}
$checks=@($verify.Checks.PSObject.Properties)
if($checks.Count -ne 17 -or @($checks|Where-Object {$_.Value -ne $true}).Count -ne 0){throw 'Server verification did not pass every reviewed check.'}
$stateBytes=Read-Held $statePath 1048576
$state=[Text.Encoding]::UTF8.GetString($stateBytes).TrimStart([char]0xFEFF)|ConvertFrom-Json
if($state.StateId -cne 'eb12c122-d728-4dc9-baec-2791aaf38f62' -or $state.Computer -ine 'MSI' -or $state.OwnerSid -cne $expectedSid -or $state.Status -cne 'SERVER_STARTED_CLIENT_LOGIN_NOT_TESTED'){throw 'Setup identity/status changed.'}
$publicBytes=Read-Held $keyPath 4096
$line=[Text.Encoding]::UTF8.GetString($publicBytes).Trim()
if($line -cnotmatch '^ssh-ed25519 ([A-Za-z0-9+/]+={0,2})(?: [^\r\n]*)?$'){throw 'Unexpected Ed25519 public-key format.'}
$base64=$Matches[1]
$wire=[Convert]::FromBase64String($base64)
if($wire.Length -ne 51 -or [Convert]::ToBase64String($wire) -cne $base64 -or ($wire[0..3] -join ',') -cne '0,0,0,11' -or [Text.Encoding]::ASCII.GetString($wire,4,11) -cne 'ssh-ed25519' -or ($wire[15..18] -join ',') -cne '0,0,0,32'){throw 'Unexpected Ed25519 SSH wire structure.'}
$h=[Security.Cryptography.SHA256]::Create()
try{$fingerprint='SHA256:'+([Convert]::ToBase64String($h.ComputeHash($wire))).TrimEnd('=')}finally{$h.Dispose()}
foreach($values in @(@($verify.HostPublicKeyFingerprints),@($state.HostKeyFingerprints))){
    $ed=@($values|Where-Object {$_ -match '\(ED25519\)$'})
    if($ed.Count -ne 1 -or $ed[0] -cnotmatch '^256 (SHA256:[A-Za-z0-9+/]+) .*\(ED25519\)$' -or $Matches[1] -cne $fingerprint){throw 'Public-key fingerprint differs from verification/setup.'}
}
if($fingerprint -cne $expectedFingerprint){throw 'Public-key fingerprint differs from reviewed pin.'}
$receipt=[ordered]@{
    Format='msi-public-host-key-pin-v1';Status='PUBLIC_HOST_KEY_PIN_VERIFIED';CapturedUtc=[DateTime]::UtcNow.ToString('o')
    Computer=[Environment]::MachineName;Account=$identity.Name;Sid=$identity.User.Value
    ReaderElevated=([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    PublicKeyPath=$keyPath;PublicKeyFileBytes=$publicBytes.Length;PublicKeyFileSha256=(Hash $publicBytes)
    Algorithm='ssh-ed25519';Base64=$base64;Fingerprint=$fingerprint;TrustSource='MSI_LOCAL_PUBLIC_KEY_READ_AND_FINGERPRINT_VERIFIED'
    ServerVerifyLogPath=$verifyPath;ServerVerifyLogSha256=(Hash $verifyBytes);ServerVerifyFinishedUtc=$verify.FinishedUtc;ServerVerifyStatus=$verify.Status
    SetupStatePath=$statePath;SetupStateSha256=(Hash $stateBytes);SetupStateId=$state.StateId
    PrivateKeyRead=$false;NetworkConnectionAttempted=$false;SettingsChanged=$false;ClientLoginTested=$false
}
$out=Join-Path $PSScriptRoot ('ssh-public-host-key-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N')+'.json')
Plain $PSScriptRoot
$bytes=[Text.Encoding]::UTF8.GetBytes(($receipt|ConvertTo-Json -Depth 5))
$s=[IO.FileStream]::new($out,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$s.Write($bytes,0,$bytes.Length);$s.Flush($true)}finally{$s.Dispose()}
if((Hash ([IO.File]::ReadAllBytes($out))) -cne (Hash $bytes)){throw 'Public receipt readback failed.'}
Write-Output $out
