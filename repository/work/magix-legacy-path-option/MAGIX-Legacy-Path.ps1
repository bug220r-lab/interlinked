#requires -Version 5.1
<# Fixed LOCAL cache junction for the already fetched MAGIX project.
No network media, project edits, file copies, policy changes or recursive deletion. #>
[CmdletBinding()]
param([ValidateSet('Check','Enable','Restore','ApplyElevated','RestoreElevated')][string]$Mode='Check',
      [string]$RequestPath='', [string]$RequestSha256='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

function Require([bool]$Value,[string]$Message) { if(-not $Value){throw $Message} }
function Hash-Bytes([byte[]]$Bytes) {
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
function New-Json([string]$Path,$Object) {
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Object|ConvertTo-Json -Depth 12))
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
    return Hash-Bytes $bytes
}
function Read-Json([string]$Path,[long]$MaximumBytes=65536) {
    $attributes=[MagixPathNative]::Attributes($Path)
    Require ($attributes -ge 0 -and ($attributes -band (0x10 -bor 0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) 'Control receipt must be an ordinary local file.'
    $stream=Open-PlainReader $Path
    try {
        Require ($stream.Length -le $MaximumBytes) 'Unexpectedly large receipt.'
        $reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true)
        try {$value=$reader.ReadToEnd()|ConvertFrom-Json} finally {$reader.Dispose()}
    } finally {$stream.Dispose()}
    Require ($null -ne $value -and $value -is [pscustomobject]) 'Expected a JSON receipt object.'
    return $value
}
function Initialize-Native {
    if('MagixPathNative' -as [type]){return}
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class MagixPathNative {
 [StructLayout(LayoutKind.Sequential)] public struct Info {
  public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh,
   Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFileW(string p,uint a,uint s,IntPtr x,uint d,uint f,IntPtr t);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFileAttributesW(string p);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle h,out Info i);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool DeviceIoControl(SafeFileHandle h,uint c,IntPtr i,uint n,byte[] o,uint z,out uint r,IntPtr v);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateDirectoryW(string p,IntPtr a);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetFileInformationByHandle(SafeFileHandle h,int c,byte[] i,uint n);
 static void Error() {throw new Win32Exception(Marshal.GetLastWin32Error());}
 public static long Attributes(string p) {uint a=GetFileAttributesW(p);if(a!=0xffffffff)return a;int e=Marshal.GetLastWin32Error();if(e==2||e==3)return -1;throw new Win32Exception(e);}
 public static SafeFileHandle Open(string p,bool delete) {
  // OPEN_REPARSE_POINT: the handle refers to the link itself, never its target.
  SafeFileHandle h=CreateFileW(p,0x80U|(delete?0x10000U:0),3,IntPtr.Zero,3,0x02200000,IntPtr.Zero);
  if(h.IsInvalid){h.Dispose();Error();}return h;
 }
 public static SafeFileHandle OpenRead(string p) {
  SafeFileHandle h=CreateFileW(p,0x80000000,1,IntPtr.Zero,3,0x00300000,IntPtr.Zero);
  if(h.IsInvalid){h.Dispose();Error();}return h;
 }
 public static Info Stat(SafeFileHandle h) {Info i;if(!GetFileInformationByHandle(h,out i))Error();return i;}
 public static string Identity(SafeFileHandle h) {Info i=Stat(h);return i.Volume.ToString("x8")+":"+i.IndexHigh.ToString("x8")+i.IndexLow.ToString("x8");}
 public static string Target(SafeFileHandle h) {
  byte[] b=new byte[16384];uint r;if(!DeviceIoControl(h,0x000900A8,IntPtr.Zero,0,b,(uint)b.Length,out r,IntPtr.Zero))Error();
  if(r<16||BitConverter.ToUInt32(b,0)!=0xA0000003)throw new Exception("Not a directory junction.");
  int o=BitConverter.ToUInt16(b,8),n=BitConverter.ToUInt16(b,10);
  if(n%2!=0||16+o+n>r)throw new Exception("Invalid junction metadata.");
  return System.Text.Encoding.Unicode.GetString(b,16+o,n);
 }
 public static void NewDirectory(string p) {if(!CreateDirectoryW(p,IntPtr.Zero))Error();}
 public static void NewJunction(string p,string target) {
  if(!System.Text.RegularExpressions.Regex.IsMatch(target,@"^[A-Za-z]:\\")||System.IO.Path.GetFullPath(target)!=target)throw new Exception("Junction target must be an exact local absolute path.");
  NewDirectory(p);
  using(SafeFileHandle h=CreateFileW(p,0x40000000,3,IntPtr.Zero,3,0x02200000,IntPtr.Zero)) {
   if(h.IsInvalid)Error();
   byte[] sub=System.Text.Encoding.Unicode.GetBytes(@"\??\"+target),print=System.Text.Encoding.Unicode.GetBytes(target);
   byte[] b=new byte[16+sub.Length+2+print.Length+2];
   Buffer.BlockCopy(BitConverter.GetBytes(0xA0000003U),0,b,0,4);
   Buffer.BlockCopy(BitConverter.GetBytes((ushort)(b.Length-8)),0,b,4,2);
   Buffer.BlockCopy(BitConverter.GetBytes((ushort)sub.Length),0,b,10,2);
   Buffer.BlockCopy(BitConverter.GetBytes((ushort)(sub.Length+2)),0,b,12,2);
   Buffer.BlockCopy(BitConverter.GetBytes((ushort)print.Length),0,b,14,2);
   Buffer.BlockCopy(sub,0,b,16,sub.Length);Buffer.BlockCopy(print,0,b,18+sub.Length,print.Length);
   IntPtr input=Marshal.AllocHGlobal(b.Length);
   try {Marshal.Copy(b,0,input,b.Length);uint r;if(!DeviceIoControl(h,0x000900A4,input,(uint)b.Length,new byte[0],0,out r,IntPtr.Zero))Error();}
   finally {Marshal.FreeHGlobal(input);}
  }
 }
 public static void DeleteExactLink(SafeFileHandle h,string identity,string target) {
  if((Stat(h).Attributes&0x410)!=0x410||Identity(h)!=identity||Target(h)!=target)throw new Exception("Owned link identity/target differs; nothing removed.");
  // FileDispositionInfo, on the held OPEN_REPARSE_POINT handle only.
  if(!SetFileInformationByHandle(h,4,new byte[]{1},1))Error();
 }
}
'@
}
function Hold-PlainDirectory([string]$Path) {
    $handle=[MagixPathNative]::Open($Path,$false)
    try {
        $attributes=[MagixPathNative]::Stat($handle).Attributes
        Require (($attributes -band 0x10) -ne 0 -and ($attributes -band (0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) ('Non-ordinary directory refused: '+$Path)
        return $handle
    } catch {$handle.Dispose();throw}
}
function Hold-LocalAncestors([string]$Path) {
    Require ($Path -cmatch '^[A-Za-z]:\\' -and [IO.Path]::GetFullPath($Path) -ceq $Path) 'Expected a canonical local path.'
    $paths=New-Object 'Collections.Generic.List[string]'
    $current=$Path
    while($current){$paths.Add($current);$parent=[IO.Directory]::GetParent($current);$current=if($parent){$parent.FullName}else{$null}}
    $held=New-Object 'Collections.Generic.List[IDisposable]'
    try {for($i=$paths.Count-1;$i -ge 0;$i--){$held.Add((Hold-PlainDirectory $paths[$i]))};return ,$held}
    catch {foreach($h in $held){$h.Dispose()};throw}
}
function Assert-NoProfileBinding([string[]]$ProfilePaths) {
    foreach($path in $ProfilePaths){
        $expanded=[IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($path)).TrimEnd('\')
        Require ($expanded -ine 'C:\Users\jlewi' -and -not $expanded.StartsWith('C:\Users\jlewi\',[StringComparison]::OrdinalIgnoreCase)) 'A Windows profile is registered at/beneath the legacy path; this option is refused.'
    }
}
function Assert-FreshLegacyRoot([string]$Root) {
    Require ([MagixPathNative]::Attributes($Root) -eq -1) 'The legacy parent already exists (including a dangling link). It will not be replaced or reused.'
}
function Assert-ProfileUnbound {
    $paths=@(Get-ChildItem -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' | ForEach-Object {
        $p=Get-ItemProperty -LiteralPath $_.PSPath
        if($p.PSObject.Properties.Name -contains 'ProfileImagePath'){[string]$p.ProfileImagePath}
    })
    Assert-NoProfileBinding $paths
    Assert-NoProfileBinding @(Get-CimInstance Win32_UserProfile | ForEach-Object {[string]$_.LocalPath})
    Require (@(Get-CimInstance Win32_UserAccount -Filter "LocalAccount=True AND Name='jlewi'").Count -eq 0) 'A local jlewi account exists; do not occupy its potential profile path.'
}
function Assert-MagixClosed {
    $running=@(Get-Process | Where-Object {$_.ProcessName -imatch '^(videodeluxe|movieeditpro)([_.-].*)?$'})
    Require ($running.Count -eq 0) 'Close MAGIX, including any crash/recovery window, before this operation. Nothing will be closed automatically.'
}
function Open-PlainReader([string]$Path) {
    $handle=[MagixPathNative]::OpenRead($Path)
    try {
        Require (([MagixPathNative]::Stat($handle).Attributes -band (0x10 -bor 0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) 'Read handle must address an ordinary local file, without following a reparse point.'
        return [IO.FileStream]::new($handle,[IO.FileAccess]::Read)
    } catch {$handle.Dispose();throw}
}
function Assert-CacheIdentity($Owner,$State) {
    Require ($Owner.format -ceq 'project-library-owned-v1' -and $State.format -ceq 'project-library-cache-v1') 'Invalid cache ownership/state format.'
    foreach($value in @($Owner,$State)) {
        Require ($value.projectId -ceq $script:ProjectId -and $value.catalogId -ceq 'msi-project-library-v1') 'Cache belongs to a different project/catalog.'
        Require ($value.library -iin @('\\MSI\ProjectLibrary','\\MSI\ProjectLibrary\') -and $value.returns -iin @('\\MSI\ProjectReturns','\\MSI\ProjectReturns\')) 'Cache library/Returns identity differs.'
    }
    Require ($State.status -ceq 'READY' -and -not ($State.PSObject.Properties.Name -contains 'pendingSave')) 'This fixed activation requires a READY cache without pending save. Use the normal client first; local edits are never overwritten here.'
}
function Pin-LocalFile([string]$Path,[long]$Length,[string]$ExpectedSha256,[Collections.Generic.List[IDisposable]]$DirectoryHandles) {
    Require ($null -ne $DirectoryHandles) 'Caller must retain all source ancestor guards.'
    foreach($handle in (Hold-LocalAncestors ([IO.Path]::GetDirectoryName($Path)))){$DirectoryHandles.Add($handle)}
    $attributes=[MagixPathNative]::Attributes($Path)
    Require ($attributes -ge 0 -and ($attributes -band (0x10 -bor 0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) 'Expected an ordinary local file, not a link/cloud placeholder.'
    $stream=Open-PlainReader $Path
    try {
        Require ($stream.Length -eq $Length) ('File length differs; preserve/review local edits: '+$Path)
        $hash=[Security.Cryptography.SHA256]::Create()
        try {$digest=([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
        Require ($digest -ceq $ExpectedSha256) ('File hash differs; preserve/review local edits: '+$Path)
        $stream.Position=0;return $stream
    } catch {$stream.Dispose();throw}
}
function Read-ReadyCache {
    $owner=Read-Json (Join-Path $script:CacheRoot 'OWNER.json')
    $state=Read-Json (Join-Path $script:CacheRoot 'state.json') 4194304
    Assert-CacheIdentity $owner $state
    return [ordered]@{
        ProjectId=$script:ProjectId;Status='READY';Workspace=$script:CacheRoot
        OwnerSha256=(Get-FileHash -LiteralPath (Join-Path $script:CacheRoot 'OWNER.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        StateSha256=(Get-FileHash -LiteralPath (Join-Path $script:CacheRoot 'state.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
function Assert-OwnedReceipt($Owner) {
    Require ($Owner.Format -ceq 'magix-local-junction-owner-v1' -and $Owner.Computer -ceq 'THIS-MACHINE' -and $Owner.LegacyRoot -ceq $script:LegacyRoot -and $Owner.Link -ceq $script:Link -and $Owner.Target -ceq $script:Target -and $Owner.ProjectId -ceq $script:ProjectId -and $Owner.OwnerSid -ceq $script:Identity.User.Value -and $Owner.RequestId -cmatch '^[a-f0-9]{32}$') 'Owner receipt identity/path differs; nothing is changed.'
}
function Read-OwnedInstallation {
    $guards=Hold-LocalAncestors $script:OneDrive
    try {
        $owner=Read-Json (Join-Path $script:LegacyRoot '.MAGIX-Local-Junction-Owner.json');Assert-OwnedReceipt $owner
        $rootHandle=Hold-PlainDirectory $script:LegacyRoot;$parentHandle=Hold-PlainDirectory $script:OneDrive
        try {Require ([MagixPathNative]::Identity($rootHandle) -ceq $owner.RootIdentity -and [MagixPathNative]::Identity($parentHandle) -ceq $owner.ParentIdentity) 'Owned parent identities differ.'}
        finally {$rootHandle.Dispose();$parentHandle.Dispose()}
        $linkHandle=[MagixPathNative]::Open($script:Link,$false)
        try {Require ([MagixPathNative]::Identity($linkHandle) -ceq $owner.LinkIdentity -and [MagixPathNative]::Target($linkHandle) -ceq ('\??\'+$script:Target)) 'Exact owned junction identity/target differs.'}
        finally {$linkHandle.Dispose()}
        return $owner
    } finally {foreach($h in $guards){$h.Dispose()}}
}
function Invoke-Elevated($Request,[string]$RequestFile,[string]$Digest) {
    $ps='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    $action=if($Request.Action -ceq 'Enable'){'ApplyElevated'}else{'RestoreElevated'}
    $args='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Mode '+$action+' -RequestPath "'+$RequestFile+'" -RequestSha256 '+$Digest
    $child=Start-Process -FilePath $ps -ArgumentList $args -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    Require ($child.ExitCode -eq 0) 'The elevated operation did not complete; preserve its receipts for review.'
}

# Tests may dot-source function definitions. This never applies a mapping.
if($MyInvocation.InvocationName -eq '.'){return}
Initialize-Native
$script:LegacyRoot='C:\Users\jlewi'
$script:OneDrive='C:\Users\jlewi\OneDrive'
$script:Link='C:\Users\jlewi\OneDrive\Documents'
$script:ProjectId='magix-redacted-sample-test-7cfd6b1eea'
$script:CacheRoot='C:\Users\Jude\AppData\Local\ProjectLibrary\Cache\'+$script:ProjectId
$script:Target=$script:CacheRoot+'\Files\Drive_C\Users\jlewi\OneDrive\Documents'
$script:MediaRelative='MAGIX\Video deluxe 2021 Plus Steam Edition\redacted sample test.mp4'
$script:MvpRelative='redacted sample test\redacted sample test .MVP'
$script:Identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=([Security.Principal.WindowsPrincipal]$script:Identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
$held=New-Object 'Collections.Generic.List[IDisposable]'
$result=$null;$run=$null;$code=2
try {
    Require ([Environment]::MachineName -ceq 'THIS-MACHINE' -and [IO.Path]::GetFullPath($PSScriptRoot) -ceq $kit) 'Run this option only from the local new-PC MSI-Project-Library kit.'
    Require ([Environment]::GetFolderPath('UserProfile') -ieq 'C:\Users\Jude') 'Unexpected current Windows profile.'
    foreach($h in (Hold-LocalAncestors $kit)){$held.Add($h)}
    Assert-MagixClosed
    $scriptHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if($Mode -in @('ApplyElevated','RestoreElevated')) {
        Require $admin 'This internal step requires the requested elevation.'
        Require ($RequestSha256 -cmatch '^[a-f0-9]{64}$') 'Missing request pin.'
        $run=[IO.Path]::GetDirectoryName($RequestPath)
        Require ([IO.Path]::GetDirectoryName($run) -ceq $kit -and [IO.Path]::GetFileName($run) -cmatch '^MAGIX-Path-Run-[a-f0-9]{32}$' -and [IO.Path]::GetFileName($RequestPath) -ceq 'request.json') 'Unexpected request path.'
        $held.Add((Hold-PlainDirectory $run))
        Require ((Get-FileHash -LiteralPath $RequestPath -Algorithm SHA256).Hash -ieq $RequestSha256) 'Request bytes changed after normal-user preflight.'
        $request=Read-Json $RequestPath
        Require ($request.Format -ceq 'magix-local-junction-request-v1' -and $request.OwnerSid -ceq $script:Identity.User.Value -and $request.Computer -ceq 'THIS-MACHINE' -and $request.ScriptSha256 -ceq $scriptHash -and $request.RequestId -ceq ([IO.Path]::GetFileName($run).Substring(15))) 'Preflight identity/helper pin differs.'
        $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($request.Utc)).TotalMinutes
        Require ($age -ge 0 -and $age -le 10) 'Normal-user preflight expired; run the normal launcher again.'
        $parent=Get-Process -Id $request.ParentProcessId
        Require ($parent.ProcessName -ieq 'powershell' -and $parent.StartTime.ToUniversalTime().ToString('o') -ceq $request.ParentProcessStartedUtc) 'The normal-user preflight process is no longer active.'
        Require (($Mode -ceq 'ApplyElevated' -and $request.Action -ceq 'Enable') -or ($Mode -ceq 'RestoreElevated' -and $request.Action -ceq 'Restore')) 'Request action differs.'
        $result=[ordered]@{Format='magix-local-junction-elevated-result-v1';RequestId=$request.RequestId;Action=$request.Action;Status='STARTING';Utc=[DateTime]::UtcNow.ToString('o');SourceDataChanged=$false;GlobalPolicyChanged=$false;Failure=$null}
        Assert-ProfileUnbound
        if($Mode -ceq 'ApplyElevated') {
            Require ($request.NormalUserLocalCacheVerified -eq $true) 'Normal-user local cache preflight is required before elevation.'
            foreach($h in (Hold-LocalAncestors $script:Target)){$held.Add($h)}
            $ready=Read-ReadyCache
            Require ($ready.OwnerSha256 -ceq $request.Cache.OwnerSha256 -and $ready.StateSha256 -ceq $request.Cache.StateSha256) 'Cache metadata changed since normal-user preflight.'
            $held.Add((Hold-PlainDirectory 'C:\'));$held.Add((Hold-PlainDirectory 'C:\Users'))
            Assert-FreshLegacyRoot $script:LegacyRoot
            # An intent precedes all creation; interrupted setup never adopts existing paths.
            [void](New-Json (Join-Path $run 'creation-intent.json') ([ordered]@{RequestId=$request.RequestId;LegacyRoot=$script:LegacyRoot;Link=$script:Link;Target=$script:Target;ProjectId=$script:ProjectId}))
            [MagixPathNative]::NewDirectory($script:LegacyRoot)
            $rootHandle=Hold-PlainDirectory $script:LegacyRoot;$held.Add($rootHandle)
            [MagixPathNative]::NewDirectory($script:OneDrive)
            $parentHandle=Hold-PlainDirectory $script:OneDrive;$held.Add($parentHandle)
            Assert-MagixClosed
            [MagixPathNative]::NewJunction($script:Link,$script:Target)
            $linkHandle=[MagixPathNative]::Open($script:Link,$false);$held.Add($linkHandle)
            Require ([MagixPathNative]::Target($linkHandle) -ceq ('\??\'+$script:Target)) 'Created junction has an unexpected target.'
            $owner=[ordered]@{Format='magix-local-junction-owner-v1';Computer='THIS-MACHINE';OwnerSid=$script:Identity.User.Value;RequestId=$request.RequestId;CreatedUtc=[DateTime]::UtcNow.ToString('o');ProjectId=$script:ProjectId;LegacyRoot=$script:LegacyRoot;Link=$script:Link;Target=$script:Target;RootIdentity=[MagixPathNative]::Identity($rootHandle);ParentIdentity=[MagixPathNative]::Identity($parentHandle);LinkIdentity=[MagixPathNative]::Identity($linkHandle);ScriptSha256=$scriptHash;RequestSha256=$RequestSha256}
            $result.OwnerReceiptSha256=New-Json (Join-Path $script:LegacyRoot '.MAGIX-Local-Junction-Owner.json') $owner
            [void](New-Json (Join-Path $run 'owner.json') $owner)
            $result.Status='EXACT_LOCAL_JUNCTION_CREATED_NORMAL_USER_READBACK_PENDING'
        } else {
            foreach($h in (Hold-LocalAncestors $script:OneDrive)){$held.Add($h)}
            $owner=Read-OwnedInstallation
            Require ($request.OwnerReceiptSha256 -ceq (Get-FileHash -LiteralPath (Join-Path $script:LegacyRoot '.MAGIX-Local-Junction-Owner.json') -Algorithm SHA256).Hash.ToLowerInvariant()) 'Owner receipt changed after restore preflight.'
            Assert-MagixClosed
            $linkHandle=[MagixPathNative]::Open($script:Link,$true)
            try {[MagixPathNative]::DeleteExactLink($linkHandle,$owner.LinkIdentity,('\??\'+$script:Target))}finally{$linkHandle.Dispose()}
            Require ([MagixPathNative]::Attributes($script:Link) -eq -1) 'Owned junction removal is not confirmed.'
            # No target following or recursive deletion. Parents/owner audit remain.
            $result.Status='EXACT_OWNED_JUNCTION_REMOVED_PARENTS_AND_RECEIPTS_RETAINED'
        }
        $code=0
    } else {
        Require (-not $admin) 'Run this launcher normally, without administrator mode, for the local user preflight.'
        $id=[guid]::NewGuid().ToString('N');$run=Join-Path $kit ('MAGIX-Path-Run-'+$id)
        [MagixPathNative]::NewDirectory($run);$held.Add((Hold-PlainDirectory $run))
        $result=[ordered]@{Format='magix-local-junction-result-v1';Action=$Mode;Status='PREFLIGHT';RequestId=$id;Utc=[DateTime]::UtcNow.ToString('o');Computer='THIS-MACHINE';OwnerSid=$script:Identity.User.Value;GlobalPolicyChanged=$false;SourceDataChanged=$false;ExistingProjectCacheChanged=$false;NetworkAccessPerformed=$false;MagixOpened=$false;Failure=$null}
        Assert-ProfileUnbound
        $process=Get-Process -Id $PID
        $request=[ordered]@{Format='magix-local-junction-request-v1';RequestId=$id;Action=$Mode;Utc=[DateTime]::UtcNow.ToString('o');Computer='THIS-MACHINE';OwnerSid=$script:Identity.User.Value;ScriptSha256=$scriptHash;NormalUserLocalCacheVerified=$false;ParentProcessId=$PID;ParentProcessStartedUtc=$process.StartTime.ToUniversalTime().ToString('o');OwnerReceiptSha256=$null;Cache=$null}
        if($Mode -ceq 'Restore') {
            [void](Read-OwnedInstallation)
            $request.OwnerReceiptSha256=(Get-FileHash -LiteralPath (Join-Path $script:LegacyRoot '.MAGIX-Local-Junction-Owner.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        } else {
            Assert-FreshLegacyRoot $script:LegacyRoot
            foreach($h in (Hold-LocalAncestors $script:Target)){$held.Add($h)}
            $lockPath=Join-Path $script:CacheRoot '.client.lock'
            Require ([MagixPathNative]::Attributes($lockPath) -ge 0 -and ([MagixPathNative]::Attributes($lockPath) -band (0x410 -bor 0x1000 -bor 0x40000 -bor 0x400000)) -eq 0) 'Missing or non-ordinary client lock.'
            $held.Add([IO.FileStream]::new($lockPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None))
            $request.Cache=Read-ReadyCache;$result.Cache=$request.Cache
            Write-Host 'Verifying the existing local MVP and 150 MiB video. No download or project edit is performed.'
            $held.Add((Pin-LocalFile (Join-Path $script:Target $script:MvpRelative) 6387360 'e35401797e782602860d2f03da15fcf619b6907fff773b53a6c15dc733a62ea0' $held))
            $held.Add((Pin-LocalFile (Join-Path $script:Target $script:MediaRelative) 157400995 'b4a78f2c98cda9f3249af0ae130da434411e874392ff3d9c210f92e731bc0b02' $held))
            $request.NormalUserLocalCacheVerified=$true;$result.LocalProjectAndMediaSha256Verified=$true
        }
        $requestFile=Join-Path $run 'request.json';$digest=New-Json $requestFile $request
        if($Mode -ceq 'Check') {$result.Status='LOCAL_PREFLIGHT_PASSED_NO_JUNCTION_CREATED';$code=0}
        else {
            Invoke-Elevated $request $requestFile $digest
            $elevated=Read-Json (Join-Path $run 'elevated-result.json')
            Require ($elevated.RequestId -ceq $id) 'Elevation receipt belongs to another request.'
            if($Mode -ceq 'Enable') {
                Require ($elevated.Status -ceq 'EXACT_LOCAL_JUNCTION_CREATED_NORMAL_USER_READBACK_PENDING') 'Junction creation was not verified.'
                [void](Read-OwnedInstallation)
                # Intentionally traverse only the independently verified owned junction.
                $media=[IO.FileStream]::new((Join-Path $script:Link $script:MediaRelative),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                try {Require ($media.Length -eq 157400995) 'Mapped media length differs.';$buffer=New-Object byte[] 65536;$read=$media.Read($buffer,0,$buffer.Length);Require ($read -eq 65536 -and (Hash-Bytes $buffer) -ceq '892be4685b78f09d3b73077ade6bd4d1aa200d6e8a86a321a26b3d45fc2416e9') 'Mapped local media readback differs.'}finally{$media.Dispose()}
                $result.Status='LOCAL_JUNCTION_AND_MEDIA_READ_VERIFIED_MAGIX_REOPEN_PENDING'
                $result.OpenManuallyAfterSuccess=Join-Path $script:Target $script:MvpRelative
            } else {Require ($elevated.Status -ceq 'EXACT_OWNED_JUNCTION_REMOVED_PARENTS_AND_RECEIPTS_RETAINED') 'Exact junction restore was not verified.';$result.Status=$elevated.Status}
            $code=0
        }
    }
} catch {
    if($null -ne $result){$result.Status='BLOCKED_OR_INCOMPLETE';$result.Failure=$_.Exception.Message}
    Write-Host ('Stopped: '+$_.Exception.Message)
} finally {
    if($null -ne $result -and $run){
        $name=if($Mode -in @('ApplyElevated','RestoreElevated')){'elevated-result.json'}else{'result.json'}
        $result.CompletedUtc=[DateTime]::UtcNow.ToString('o')
        try {[void](New-Json (Join-Path $run $name) $result);Write-Host ('Receipt: '+(Join-Path $run $name))}catch{Write-Host ('Receipt write failed: '+$_.Exception.Message);$code=2}
    }
    foreach($h in $held){$h.Dispose()}
}
if($null -ne $result){Write-Host $result.Status}
exit $code
