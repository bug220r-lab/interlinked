#requires -Version 5.1
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$helper=Join-Path $PSScriptRoot 'MAGIX-Legacy-Path.ps1'
$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
. $helper
Initialize-Native
$checks=New-Object 'Collections.Generic.List[string]'
$checks.Add('Windows PowerShell 5.1 syntax and native class compile')
function Reject([scriptblock]$Action,[string]$Name){$failed=$false;try{& $Action}catch{$failed=$true};Require $failed ('Expected rejection: '+$Name);$checks.Add($Name)}
$root=Join-Path $PSScriptRoot ('Fixture-'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root)|Out-Null
$target=Join-Path $root 'Local Target';[IO.Directory]::CreateDirectory($target)|Out-Null
[IO.File]::WriteAllBytes((Join-Path $target 'original.txt'),[Text.Encoding]::UTF8.GetBytes('Owned synthetic source; preserve these exact bytes.'))
$original=(Get-FileHash -LiteralPath (Join-Path $target 'original.txt') -Algorithm SHA256).Hash
Assert-FreshLegacyRoot (Join-Path $root 'absent');$checks.Add('Absent local root accepted')
Reject {Assert-FreshLegacyRoot $target} 'Existing ordinary root refused without overwrite'
Assert-NoProfileBinding @('C:\Users\Jude','C:\Users\Other')
Reject {Assert-NoProfileBinding @('C:\Users\Jude','c:\users\JLEWI\')} 'Registered legacy Windows profile refused even when folder is absent'
Reject {Assert-NoProfileBinding @('C:\Users\jlewi\.')} 'Dot-normalized profile binding refused'
Reject {Assert-NoProfileBinding @('C:\Users\jlewi\nested-profile')} 'Profile binding beneath the legacy parent refused'
$script:ProjectId='magix-redacted-sample-test-7cfd6b1eea'
$owner=[pscustomobject]@{format='project-library-owned-v1';projectId=$script:ProjectId;catalogId='msi-project-library-v1';library='\\MSI\ProjectLibrary\';returns='\\MSI\ProjectReturns\'}
$state=[pscustomobject]@{format='project-library-cache-v1';projectId=$script:ProjectId;catalogId='msi-project-library-v1';library='\\MSI\ProjectLibrary\';returns='\\MSI\ProjectReturns\';status='READY'}
Assert-CacheIdentity $owner $state;$checks.Add('Actual trailing UNC identity accepted without network access')
$state.status='FETCHING';Reject {Assert-CacheIdentity $owner $state} 'Incomplete cache rejected';$state.status='READY'
$owner.library='\\192.168.247.1\ProjectLibrary';Reject {Assert-CacheIdentity $owner $state} 'Different cache server alias rejected';$owner.library='\\MSI\ProjectLibrary\'
$payload=Join-Path $target 'original.txt'
$guards=New-Object 'Collections.Generic.List[IDisposable]'
$held=Pin-LocalFile $payload (Get-Item -LiteralPath $payload).Length $original.ToLowerInvariant() $guards
try {Reject {$w=[IO.FileStream]::new($payload,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$w.Dispose()} 'Verified source handle prevents concurrent modification'}finally{$held.Dispose();foreach($guard in $guards){$guard.Dispose()};$guards.Clear()}
try {Reject {Pin-LocalFile $payload (Get-Item -LiteralPath $payload).Length ('0'*64) $guards} 'Changed content hash rejected without replacing data'}finally{foreach($guard in $guards){$guard.Dispose()};$guards.Clear()}
$link=Join-Path $root 'Legacy Documents'
$junctionCreated=$false
try {
    [MagixPathNative]::NewJunction($link,$target);$junctionCreated=$true
    $read=[MagixPathNative]::Open($link,$false)
    try {$identity=[MagixPathNative]::Identity($read);Require ([MagixPathNative]::Target($read) -ceq ('\??\'+$target)) 'Junction metadata differs.'}finally{$read.Dispose()}
    Require ((Get-FileHash -LiteralPath (Join-Path $link 'original.txt') -Algorithm SHA256).Hash -ceq $original) 'Local read-through hash differs.'
    $checks.Add('Actual local directory junction reads exact source bytes')
    Reject {Pin-LocalFile (Join-Path $link 'original.txt') (Get-Item -LiteralPath $payload).Length $original.ToLowerInvariant() $guards} 'Matching file behind nested junction source parent refused'
    Reject {Read-Json $link} 'Reparse control leaf refused before reading JSON'
    Reject {Assert-FreshLegacyRoot $link} 'Existing junction refused without traversal/replacement'
    Reject {Hold-PlainDirectory $link} 'Junction ancestor rejected by plain-directory guard'
    $delete=[MagixPathNative]::Open($link,$true)
    try {
        Reject {[MagixPathNative]::DeleteExactLink($delete,'incorrect',('\??\'+$target))} 'Wrong owned file identity cannot delete junction'
        Reject {[MagixPathNative]::DeleteExactLink($delete,$identity,('\??\'+(Join-Path $root 'Other')))} 'Different target cannot delete junction'
        [MagixPathNative]::DeleteExactLink($delete,$identity,('\??\'+$target))
    }finally{$delete.Dispose()}
    Require ([MagixPathNative]::Attributes($link) -eq -1) 'Junction not removed.'
    Require ((Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash -ceq $original) 'Target source changed during exact unlink.'
    $checks.Add('Held-handle exact junction removal preserves target/source bytes')
    $dangling=Join-Path $root 'Dangling Junction';$missing=Join-Path $root 'Missing Target'
    [MagixPathNative]::NewJunction($dangling,$missing)
    Reject {Assert-FreshLegacyRoot $dangling} 'Dangling junction existence detected without Test-Path'
    $delete=[MagixPathNative]::Open($dangling,$true)
    try {[MagixPathNative]::DeleteExactLink($delete,[MagixPathNative]::Identity($delete),('\??\'+$missing))}finally{$delete.Dispose()}
    $checks.Add('Exact unlink works without target availability')
    $status='PASS'
} catch {
    $status='INCOMPLETE_NATIVE_JUNCTION_FIXTURE';$failure=$_.Exception.Message
}
$result=[ordered]@{Status=$status;Checks=$checks.ToArray();HelperSha256=(Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash.ToLowerInvariant();Fixture=$root;NativeJunctionCreated=$junctionCreated;NetworkAccess=$false;UacRequested=$false;RealProfileChanged=$false;UserProjectChanged=$false}
if($status -ne 'PASS'){$result.Failure=$failure}
[void](New-Json (Join-Path $PSScriptRoot ('test-results-'+[guid]::NewGuid().ToString('N')+'.json')) $result)
$result|ConvertTo-Json -Depth 6
if($status -ne 'PASS'){exit 1}
