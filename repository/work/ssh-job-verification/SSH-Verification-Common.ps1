Set-StrictMode -Version Latest
function Require([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
function Plain([string]$Path,[bool]$Directory=$false){
    $full=[IO.Path]::GetFullPath($Path);$walk=$full
    while($walk){
        if(Test-Path -LiteralPath $walk){$i=Get-Item -LiteralPath $walk -Force;Require (($i.Attributes -band (0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) ('Linked/cloud path refused: '+$walk)}
        $parent=[IO.Directory]::GetParent($walk);$walk=if($parent){$parent.FullName}else{$null}
    }
    if(Test-Path -LiteralPath $full){$i=Get-Item -LiteralPath $full -Force;Require (($i -is [IO.DirectoryInfo]) -eq $Directory) ('Unexpected path type: '+$full)}
    return $full
}
function Hash-Bytes([byte[]]$Bytes){$s=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($s.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$s.Dispose()}}
function Read-Small([string]$Path){$p=Plain $Path;Require ((Get-Item -LiteralPath $p).Length -le 1048576) 'Unexpectedly large control/receipt file.';return ,[IO.File]::ReadAllBytes($p)}
function Read-Json([string]$Path){$v=[Text.Encoding]::UTF8.GetString((Read-Small $Path))|ConvertFrom-Json;Require ($null -ne $v -and $v -is [pscustomobject]) 'Expected JSON object.';return $v}
function New-Bytes([string]$Path,[byte[]]$Bytes){[void](Plain $Path);$s=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$s.Write($Bytes,0,$Bytes.Length);$s.Flush($true)}finally{$s.Dispose()}}
function New-Json([string]$Path,$Value){New-Bytes $Path ([Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 15)))}
function Quote-Arg([string]$Value){
    if($Value -notmatch '[\s"]' -and $Value.Length -gt 0){return $Value}
    $value=[regex]::Replace($Value,'(\\*)"','$1$1\"');$value=[regex]::Replace($value,'(\\+)$','$1$1');return '"'+$value+'"'
}
function Load-PinnedPlan([string]$Folder){
    $path=Join-Path $Folder 'SSH-Verification-Plan.json';$bytes=Read-Small $path
    $expected=([IO.File]::ReadAllText((Join-Path $Folder 'SSH-Verification-Plan.sha256'))).Trim()
    Require ($expected -cmatch '^[a-f0-9]{64}$' -and (Hash-Bytes $bytes) -ceq $expected) 'Verification plan hash differs.'
    $p=[Text.Encoding]::UTF8.GetString($bytes)|ConvertFrom-Json
    Require ($p.Format -ceq 'msi-ssh-job-verification-v1' -and $p.Status -ceq 'READY_FOR_ACTUAL_NEW_PC_TEST') 'Server readiness and trusted public-key pin must be completed first.'
    Require ($p.FixtureId -cmatch '^[a-f0-9]{32}$' -and $p.ProjectId -cmatch '^ssh-job-check-[a-f0-9]{32}$') 'Invalid fixture identity.'
    Require ($p.ExpectedComputer -ceq 'MSI' -and $p.ExpectedSid -ceq 'S-1-5-21-3086848077-1833151167-136462239-1001') 'Unexpected MSI identity.'
    Require ($p.SshUser -ceq 'jlewi' -and $p.SshHost -ceq 'MSI' -and $p.SshAddress -ceq '192.168.247.1') 'Unexpected SSH endpoint.'
    return $p
}
function Pin-File([string]$Path,[string]$Hash,[long]$Bytes=-1){
    [void](Plain $Path);$s=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{if($Bytes -ge 0){Require ($s.Length -eq $Bytes) ('File length changed: '+$Path)};$h=[Security.Cryptography.SHA256]::Create();try{$got=([BitConverter]::ToString($h.ComputeHash($s))).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()};Require ($got -ceq $Hash) ('File hash changed: '+$Path);$s.Position=0;return $s}catch{$s.Dispose();throw}
}
