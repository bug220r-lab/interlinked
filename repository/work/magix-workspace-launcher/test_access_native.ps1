#requires -Version 5.1
# Native ACL roundtrip only on newly created, owned work fixtures. No UAC.
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'MAGIX-Workspace-Access.ps1')
. (Join-Path $PSScriptRoot '..\magix-legacy-path-option\MAGIX-Legacy-Path.ps1')
Initialize-Native
$fixture=Join-Path $PSScriptRoot ('AccessFixture-'+[guid]::NewGuid().ToString('N'))
$parent=Join-Path $fixture 'OwnedParent';$nested=Join-Path $parent 'OwnedNested';$target=Join-Path $fixture 'LocalTarget'
[IO.Directory]::CreateDirectory($nested)|Out-Null;[IO.Directory]::CreateDirectory($target)|Out-Null
$payload=Join-Path $target 'preserve.txt';[IO.File]::WriteAllText($payload,'Synthetic ACL isolation fixture. No project data.')
$link=Join-Path $nested 'Documents';[MagixPathNative]::NewJunction($link,$target)
$handle=[MagixPathNative]::Open($link,$false)
try{$linkId=[MagixPathNative]::Identity($handle)}finally{$handle.Dispose()}
$sourceHash=(Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash
$targetAcl=AclText $target;$fileAcl=(Get-Acl -LiteralPath $payload).Sddl
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rows=@()
foreach($path in @($parent,$nested)){
    $before=AclText $path;$desired=DesiredGrant $before $sid
    $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($desired,[Security.AccessControl.AccessControlSections]::Access)
    [IO.Directory]::SetAccessControl($path,$acl)
    Require ((AclText $path) -ceq $desired) 'Owned parent grant readback differs.'
    $rows+=@([pscustomobject]@{Path=$path;Before=$before;After=$desired})
}
Require ((AclText $target) -ceq $targetAcl -and (Get-Acl -LiteralPath $payload).Sddl -ceq $fileAcl) 'Non-inheriting parent grant changed target/source ACL.'
Require ((Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash -ceq $sourceHash) 'Target data changed.'
$handle=[MagixPathNative]::Open($link,$true)
try{[MagixPathNative]::DeleteExactLink($handle,$linkId,('\??\'+$target))}finally{$handle.Dispose()}
foreach($row in $rows){
    Require ((AclText $row.Path) -ceq $row.After) 'Unexpected parent change before restore.'
    $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($row.Before,[Security.AccessControl.AccessControlSections]::Access)
    [IO.Directory]::SetAccessControl($row.Path,$acl)
    Require ((AclText $row.Path) -ceq $row.Before) 'Original parent DACL not restored.'
}
Require ((AclText $target) -ceq $targetAcl -and (Get-Acl -LiteralPath $payload).Sddl -ceq $fileAcl -and (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash -ceq $sourceHash) 'Source isolation failed.'
# A second case models children inheriting only current-user RX. The fresh
# junction's security descriptor must grant its creator write access BEFORE
# the object becomes a reparse point; no target ACL is changed by path.
$beforeRestrictedCase=AclText $nested
$restricted='D:P(A;OICI;FA;;;SY)(A;OICI;GRGX;;;'+$sid+')'
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($restricted,[Security.AccessControl.AccessControlSections]::Access);[IO.Directory]::SetAccessControl($nested,$acl)
$grant=DesiredGrant (AclText $nested) $sid
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($grant,[Security.AccessControl.AccessControlSections]::Access);[IO.Directory]::SetAccessControl($nested,$acl)
$leafPlan=Join-Path $PSScriptRoot ('leaf-plan-'+[guid]::NewGuid().ToString('N')+'.json')
[void](New-Json $leafPlan ([ordered]@{parent=$nested;target=$target}))
& 'C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B -X utf8 (Join-Path $PSScriptRoot 'test_new_junction_leaf.py') $leafPlan
Require ($LASTEXITCODE -eq 0) 'Fresh junction leaf permissions failed under RX inheritance.'
Require ((AclText $target) -ceq $targetAcl -and (Get-Acl -LiteralPath $payload).Sddl -ceq $fileAcl) 'Fresh junction creation changed target ACL.'
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($beforeRestrictedCase,[Security.AccessControl.AccessControlSections]::Access);[IO.Directory]::SetAccessControl($nested,$acl)
Require ((AclText $nested) -ceq $beforeRestrictedCase) 'Fixture parent original DACL differs after RX-child case.'
$result=[ordered]@{Status='PASS';Checks=@('Actual this-folder-only grant on two owned fixture parents','Local junction target directory/file ACLs unchanged','Target payload SHA unchanged','Exact junction unlink preserved target','Both original parent DACLs restored exactly','Fresh junction creation/set-reparse/read/unlink under RX child inheritance','Creator rights apply to new link before reparse creation; target ACL unchanged');Fixture=$fixture;HelperSha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'MAGIX-Workspace-Access.ps1') -Algorithm SHA256).Hash.ToLowerInvariant();CoreSha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'magix_workspace.py') -Algorithm SHA256).Hash.ToLowerInvariant();ProductionAclChanged=$false;UacRequested=$false;NetworkAccess=$false}
[void](New-Json (Join-Path $PSScriptRoot ('access-native-tests-'+[guid]::NewGuid().ToString('N')+'.json')) $result)
$result|ConvertTo-Json -Depth 5
