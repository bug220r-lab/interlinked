#requires -Version 5.1
<# Optional access change on TWO existing helper-owned directory objects only.
No inheritance, recursive ACL changes, cache ACL changes, junction or media edits. #>
[CmdletBinding()]
param([ValidateSet('Grant','Restore','Apply')][string]$Action='Grant',[string]$RequestPath='',[string]$RequestSha256='')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function AclText([string]$Path) {
    return [IO.Directory]::GetAccessControl($Path,[Security.AccessControl.AccessControlSections]::Access).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
}
function DesiredGrant([string]$Before,[string]$Sid) {
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm($Before,[Security.AccessControl.AccessControlSections]::Access)
    $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($Sid),[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.InheritanceFlags]::None,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
    [void]$acl.AddAccessRule($rule)
    return $acl.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
}
if($MyInvocation.InvocationName -eq '.'){return}
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
$common=Join-Path $kit 'MAGIX-Legacy-Path.ps1'
if([IO.Path]::GetFullPath($PSScriptRoot) -cne $kit -or [Environment]::MachineName -cne 'THIS-MACHINE'){throw 'Run only from the local new-PC kit.'}
if((Get-FileHash -LiteralPath $common -Algorithm SHA256).Hash -ine '3a7332607a9fd59d460ca7eb0db8c5e46e892c67b9c9c63a68b03d076120f880'){throw 'Reviewed Stage1 functions differ.'}
# Stage1 has parameters with these same names; preserve the outer request.
$accessRequestPath=$RequestPath;$accessRequestSha256=$RequestSha256
. $common
$RequestPath=$accessRequestPath;$RequestSha256=$accessRequestSha256
Initialize-Native
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$sid=$identity.User.Value
$admin=([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$state=Join-Path $kit 'MAGIX-Workspace-State'
$root='C:\Users\jlewi';$parent='C:\Users\jlewi\OneDrive';$link=Join-Path $parent 'Documents'
$target='C:\Users\Jude\AppData\Local\ProjectLibrary\Cache\magix-redacted-sample-test-7cfd6b1eea\Files\Drive_C\Users\jlewi\OneDrive\Documents'
$held=New-Object 'Collections.Generic.List[IDisposable]'
$result=$null;$run=$null;$exitCode=2
try {
    Require ([Environment]::GetFolderPath('UserProfile') -ieq 'C:\Users\Jude') 'Unexpected Windows profile.'
    foreach($h in (Hold-LocalAncestors $state)){$held.Add($h)}
    foreach($h in (Hold-LocalAncestors $parent)){$held.Add($h)}
    Assert-ProfileUnbound;Assert-MagixClosed
    $installation=Read-Json (Join-Path $state 'installation.json')
    Require ($installation.identity.ownerSid -ceq $sid -and $installation.identity.legacy -ceq $root -and $installation.identity.target -ceq $target -and $installation.identity.projectId -ceq 'magix-redacted-sample-test-7cfd6b1eea') 'Installation identity differs.'
    $ownerPath=Join-Path $root '.MAGIX-Local-Junction-Owner.json'
    $owner=Read-Json $ownerPath
    Require ($owner.Computer -ceq 'THIS-MACHINE' -and $owner.OwnerSid -ceq $sid -and $owner.ScriptSha256 -ceq '3a7332607a9fd59d460ca7eb0db8c5e46e892c67b9c9c63a68b03d076120f880' -and (Get-FileHash -LiteralPath $ownerPath -Algorithm SHA256).Hash -ieq $installation.stage1OwnerSha256) 'Preserved Stage1 ownership differs.'
    $rootHandle=Hold-PlainDirectory $root;$held.Add($rootHandle)
    $parentHandle=Hold-PlainDirectory $parent;$held.Add($parentHandle)
    Require ([MagixPathNative]::Identity($rootHandle) -ceq $installation.rootId -and [MagixPathNative]::Identity($parentHandle) -ceq $installation.parentId) 'Owned directory identities differ.'
    $scriptSha=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if($Action -ceq 'Apply') {
        Require $admin 'Requested elevation is required for this internal step.'
        $applyLock=Join-Path $state '.parent-access.lock'
        if([MagixPathNative]::Attributes($applyLock) -ne -1){Require (([MagixPathNative]::Attributes($applyLock) -band (0x410 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) 'Invalid parent-access lock.'}
        $held.Add([IO.FileStream]::new($applyLock,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None))
        $run=[IO.Path]::GetDirectoryName($RequestPath)
        Require ([IO.Path]::GetDirectoryName($run) -ceq $state -and [IO.Path]::GetFileName($run) -cmatch '^Access-[a-f0-9]{32}$' -and [IO.Path]::GetFileName($RequestPath) -ceq 'request.json') 'Unexpected access request path.'
        $held.Add((Hold-PlainDirectory $run))
        Require ($RequestSha256 -cmatch '^[a-f0-9]{64}$' -and (Get-FileHash -LiteralPath $RequestPath -Algorithm SHA256).Hash -ieq $RequestSha256) 'Access request hash differs.'
        $request=Read-Json $RequestPath
        Require ($request.Format -ceq 'magix-parent-access-request-v1' -and $request.OwnerSid -ceq $sid -and $request.ScriptSha256 -ceq $scriptSha -and $request.Action -cin @('Grant','Restore') -and $request.RootIdentity -ceq $installation.rootId -and $request.ParentIdentity -ceq $installation.parentId) 'Request scope/identity differs.'
        $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($request.Utc)).TotalMinutes
        Require ($age -ge 0 -and $age -le 10) 'Access preflight expired.'
        Require (@($request.Entries).Count -eq 2 -and $request.Entries[0].Path -ceq $root -and $request.Entries[1].Path -ceq $parent) 'Only the two exact owned parent objects may change.'
        if($request.Action -ceq 'Grant'){
            $intent=Read-Json (Join-Path $state 'access-grant-intent.json')
            Require ($intent.RequestPath -ceq $RequestPath -and $intent.RequestSha256 -ceq $RequestSha256) 'Durable original grant intent differs.'
        }
        if($request.Action -ceq 'Restore'){
            Require ([MagixPathNative]::Attributes($link) -eq -1) 'Unbind the alias before restoring parent permissions.'
            $grant=Read-Json (Join-Path $state 'access-grant.json')
            Require ($grant.Status -ceq 'EXACT_PARENT_ACCESS_GRANT_VERIFIED' -and $grant.OwnerSid -ceq $sid -and $grant.RootIdentity -ceq $installation.rootId -and $grant.ParentIdentity -ceq $installation.parentId -and @($grant.Entries).Count -eq 2) 'Verified grant baseline differs.'
            for($i=0;$i -lt 2;$i++){
                Require ($request.Entries[$i].Path -ceq $grant.Entries[$i].Path -and $request.Entries[$i].Before -ceq $grant.Entries[$i].After -and $request.Entries[$i].After -ceq $grant.Entries[$i].Before) 'Restore must exactly reverse the verified owned-parent grant.'
            }
        }
        $result=[ordered]@{Format='magix-parent-access-result-v1';Status='APPLYING';Action=$request.Action;OwnerSid=$sid;RequestSha256=$RequestSha256;RootIdentity=$installation.rootId;ParentIdentity=$installation.parentId;Entries=@();RecursiveChanges=$false;CacheAclChanged=$false;Failure=$null}
        foreach($entry in $request.Entries) {
            Require ((AclText $entry.Path) -ceq $entry.Before) 'Parent ACL changed since preflight; nothing further is changed.'
            if($request.Action -ceq 'Grant'){Require ($entry.After -ceq (DesiredGrant $entry.Before $sid)) 'Grant must add only a same-user, this-folder-only rule.'}
        }
        $cacheAclBefore=if([MagixPathNative]::Attributes($target) -ne -1){foreach($h in (Hold-LocalAncestors $target)){$held.Add($h)};AclText $target}else{$null}
        foreach($entry in $request.Entries) {
            Assert-MagixClosed
            $acl=[Security.AccessControl.DirectorySecurity]::new()
            $acl.SetSecurityDescriptorSddlForm($entry.After,[Security.AccessControl.AccessControlSections]::Access)
            [IO.Directory]::SetAccessControl($entry.Path,$acl)
            $after=AclText $entry.Path
            $result.Entries+=@([pscustomobject]@{Path=$entry.Path;Before=$entry.Before;After=$after;Verified=($after -ceq $entry.After)})
            Require ($after -ceq $entry.After) 'Parent ACL readback differs; preserve before/after receipts for review.'
        }
        if($null -ne $cacheAclBefore){Require ((AclText $target) -ceq $cacheAclBefore) 'Target cache permissions changed unexpectedly.'}
        $result.Status='EXACT_PARENT_ACCESS_'+$request.Action.ToUpperInvariant()+'_VERIFIED';$exitCode=0
    } else {
        Require (-not $admin) 'Run normally. The helper requests elevation only for the bounded parent change.'
        $run=Join-Path $state ('Access-'+[guid]::NewGuid().ToString('N'));[MagixPathNative]::NewDirectory($run);$held.Add((Hold-PlainDirectory $run))
        $request=[ordered]@{Format='magix-parent-access-request-v1';Action=$Action;OwnerSid=$sid;ScriptSha256=$scriptSha;Utc=[DateTime]::UtcNow.ToString('o');RootIdentity=$installation.rootId;ParentIdentity=$installation.parentId;Entries=@()}
        if($Action -ceq 'Grant') {
            Require (-not (Test-Path -LiteralPath (Join-Path $state 'access-grant.json'))) 'An access grant is already recorded; preserve its baseline.'
            $intentPath=Join-Path $state 'access-grant-intent.json'
            if(Test-Path -LiteralPath $intentPath){
                $intent=Read-Json $intentPath
                Require ([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($intent.RequestPath)) -ceq $state -and [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($intent.RequestPath)) -cmatch '^Access-[a-f0-9]{32}$' -and [IO.Path]::GetFileName($intent.RequestPath) -ceq 'request.json') 'Unexpected retained grant request.'
                Require ((Get-FileHash -LiteralPath $intent.RequestPath -Algorithm SHA256).Hash -ieq $intent.RequestSha256) 'Retained original grant request changed.'
                $previous=Read-Json (Join-Path ([IO.Path]::GetDirectoryName($intent.RequestPath)) 'result.json')
                Require ($previous.Status -ceq 'EXACT_PARENT_ACCESS_GRANT_VERIFIED' -and $previous.RequestSha256 -ceq $intent.RequestSha256 -and $previous.OwnerSid -ceq $sid -and $previous.RootIdentity -ceq $installation.rootId -and $previous.ParentIdentity -ceq $installation.parentId -and @($previous.Entries).Count -eq 2) 'Prior grant was interrupted or cancelled; preserve the original baseline for review, without rebasing.'
                Require ($previous.Entries[0].Path -ceq $root -and $previous.Entries[1].Path -ceq $parent) 'Retained grant paths differ.'
                foreach($entry in $previous.Entries){Require ((AclText $entry.Path) -ceq $entry.After) 'Prior granted ACL changed; review the preserved baseline.'}
                [void](New-Json (Join-Path $state 'access-grant.json') $previous)
                Write-Host 'VERIFIED_GRANT_RECEIPT_RECOVERED';exit 0
            }
            foreach($path in @($root,$parent)){$before=AclText $path;$request.Entries+=@([pscustomobject]@{Path=$path;Before=$before;After=(DesiredGrant $before $sid)})}
        } else {
            Require ([MagixPathNative]::Attributes($link) -eq -1) 'Unbind the alias first.'
            $grant=Read-Json (Join-Path $state 'access-grant.json')
            Require ($grant.Status -ceq 'EXACT_PARENT_ACCESS_GRANT_VERIFIED' -and $grant.OwnerSid -ceq $sid -and $grant.RootIdentity -ceq $installation.rootId -and $grant.ParentIdentity -ceq $installation.parentId -and @($grant.Entries).Count -eq 2) 'Verified original access baseline is required.'
            Require ($grant.Entries[0].Path -ceq $root -and $grant.Entries[1].Path -ceq $parent) 'Recorded grant paths differ.'
            foreach($entry in $grant.Entries){Require ((AclText $entry.Path) -ceq $entry.After) 'Parent ACL changed after grant; automatic restore is refused.';$request.Entries+=@([pscustomobject]@{Path=$entry.Path;Before=$entry.After;After=$entry.Before})}
        }
        $file=Join-Path $run 'request.json';$hash=New-Json $file $request
        if($Action -ceq 'Grant'){[void](New-Json (Join-Path $state 'access-grant-intent.json') ([ordered]@{Format='magix-parent-access-intent-v1';RequestPath=$file;RequestSha256=$hash;OwnerSid=$sid;OriginalBaselinePreserved=$true}))}
        $args='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Action Apply -RequestPath "'+$file+'" -RequestSha256 '+$hash
        $child=Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -ArgumentList $args -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        Require ($child.ExitCode -eq 0) 'Parent access action failed or was cancelled; inspect retained request/result.'
        $done=Read-Json (Join-Path $run 'result.json')
        Require ($done.Status -ceq ('EXACT_PARENT_ACCESS_'+$Action.ToUpperInvariant()+'_VERIFIED') -and $done.RequestSha256 -ceq $hash) 'Elevated result differs.'
        if($Action -ceq 'Grant'){[void](New-Json (Join-Path $state 'access-grant.json') $done)}
        Write-Host $done.Status;$exitCode=0
    }
}catch{
    if($null -ne $result){$result.Status='INCOMPLETE_REVIEW_REQUIRED';$result.Failure=$_.Exception.Message}
    Write-Host ('Stopped: '+$_.Exception.Message)
}finally{
    if($null -ne $result){$result.CompletedUtc=[DateTime]::UtcNow.ToString('o');try{[void](New-Json (Join-Path $run 'result.json') $result)}catch{Write-Host $_.Exception.Message;$exitCode=2}}
    foreach($h in $held){$h.Dispose()}
}
exit $exitCode
