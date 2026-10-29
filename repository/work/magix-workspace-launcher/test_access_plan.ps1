#requires -Version 5.1
# Pure ACL objects / parser checks only. Never calls SetAccessControl or UAC.
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'MAGIX-Workspace-Access.ps1'
$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
. $path
$commonPath=Join-Path $PSScriptRoot '..\magix-legacy-path-option\MAGIX-Legacy-Path.ps1'
$RequestPath='C:\Synthetic\Access-123\request.json';$RequestSha256='a'*64
$accessRequestPath=$RequestPath;$accessRequestSha256=$RequestSha256
. $commonPath
$RequestPath=$accessRequestPath;$RequestSha256=$accessRequestSha256
if($RequestPath -cne 'C:\Synthetic\Access-123\request.json' -or $RequestSha256 -cne ('a'*64)){throw 'Dot-sourced Stage1 reset outer UAC arguments.'}
$before='D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)'
$sid='S-1-5-21-1-2-3-1000'
$after=DesiredGrant $before $sid
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($after)
$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
$own=@($rules|Where-Object {$_.IdentityReference.Value -eq $sid})
if($own.Count -ne 1 -or $own[0].InheritanceFlags -ne 'None' -or $own[0].PropagationFlags -ne 'None' -or $own[0].AccessControlType -ne 'Allow' -or $own[0].FileSystemRights -ne 'FullControl'){throw 'Grant is not exactly this-folder-only same-user FullControl.'}
if(@($rules|Where-Object {$_.IdentityReference.Value -in @('S-1-5-18','S-1-5-32-544','S-1-5-32-545')}).Count -ne 3){throw 'Existing ACL entries were not preserved.'}
$restored=[Security.AccessControl.DirectorySecurity]::new();$restored.SetSecurityDescriptorSddlForm($before)
if($restored.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access) -cne $before){throw 'Baseline roundtrip differs.'}
$result=[ordered]@{Status='PASS';Checks=@('PowerShell 5.1 parse','Dot-sourced Stage1 UAC request arguments preserved','Exact same-user Allow FullControl on this folder only','Existing system/admin/users ACEs preserved','Baseline SDDL roundtrip retained');HelperSha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();FilesystemAclChanged=$false;UacRequested=$false;NetworkAccess=$false}
$result|ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'access-plan-tests.json'),($result|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
