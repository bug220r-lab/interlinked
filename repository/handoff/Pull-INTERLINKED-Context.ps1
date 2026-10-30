[CmdletBinding()]
param(
    [string]$CapsuleName,
    [string]$ManifestSha256,
    [switch]$DefineOnly
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2

function Initialize-ContextReader {
    if('InterlinkedContextReader' -as [type]){return}
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
public sealed class ContextReadResult { public long Bytes; public string Sha256; public byte[] Data; }
public sealed class ContextDirectoryGuard:IDisposable {
  internal List<SafeFileHandle> Handles=new List<SafeFileHandle>();
  public void Dispose(){for(int i=Handles.Count-1;i>=0;i--) Handles[i].Dispose();Handles.Clear();}
}
public static class InterlinkedContextReader {
  [StructLayout(LayoutKind.Sequential)] struct Info {
    public uint Attr,CreationLow,CreationHigh,AccessLow,AccessHigh,WriteLow,WriteHigh,Volume,SizeHigh,SizeLow,Links,IdHigh,IdLow;
  }
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string p,uint access,uint share,IntPtr sa,uint mode,uint flags,IntPtr template);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFileAttributes(string p);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle h,out Info info);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder p,uint count,uint flags);
  const uint Bad=0x400|0x1000|0x40000|0x400000;
  static string Extended(string p){return p.StartsWith(@"\\")?@"\\?\UNC\"+p.Substring(2):@"\\?\"+p;}
  static string[] Ancestors(string p) {
    var result=new List<string>(); string current,rest;
    if(p.StartsWith(@"\\")) {
      string[] parts=p.Substring(2).Split('\\');if(parts.Length<2 || parts[0].Length==0 || parts[1].Length==0)throw new IOException("Invalid share path.");
      current=@"\\"+parts[0]+"\\"+parts[1];rest=String.Join("\\",parts,2,parts.Length-2);
    } else {
      if(p.Length<3 || !Char.IsLetter(p[0]) || p[1]!=':' || p[2]!='\\')throw new IOException("Absolute ordinary path required.");
      current=p.Substring(0,3);rest=p.Substring(3);
    }
    result.Add(current);
    if(rest.Length>0)foreach(string part in rest.Split('\\')) {
      if(part.Length==0 || part=="." || part=="..")throw new IOException("Noncanonical path refused.");
      current=current.TrimEnd('\\')+"\\"+part;result.Add(current);
    }
    return result.ToArray();
  }
  public static void Plain(string p) {
    foreach(string part in Ancestors(p)) {
      uint attr=GetFileAttributes(Extended(part));if(attr==0xffffffff)throw new Win32Exception(Marshal.GetLastWin32Error());
      if((attr&Bad)!=0)throw new IOException("Link, reparse or cloud placeholder refused.");
    }
  }
  static Info Inspect(SafeFileHandle h,string path,bool directory) {
    Info info;if(!GetFileInformationByHandle(h,out info))throw new Win32Exception(Marshal.GetLastWin32Error());
    if((info.Attr&Bad)!=0 || (((info.Attr&0x10)!=0)!=directory))throw new IOException("Unexpected reparse or object type.");
    var final=new StringBuilder(32768);uint count=GetFinalPathNameByHandle(h,final,(uint)final.Capacity,0);
    if(count==0 || count>=final.Capacity || !String.Equals(final.ToString().TrimEnd('\\'),Extended(path).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase))throw new IOException("Opened object resolves outside the expected path.");
    return info;
  }
  public static ContextDirectoryGuard Guard(string directory) {
    var guard=new ContextDirectoryGuard();
    try {
      Plain(directory);
      foreach(string part in Ancestors(directory)) {
        var h=CreateFile(Extended(part),0x80000000,1,IntPtr.Zero,3,0x02300000,IntPtr.Zero);
        if(h.IsInvalid){h.Dispose();throw new Win32Exception(Marshal.GetLastWin32Error());}
        guard.Handles.Add(h);Inspect(h,part,true);
      }
      return guard;
    }catch{guard.Dispose();throw;}
  }
  public static ContextReadResult Read(string path,long maximum) {
    Plain(path);
    using(var h=CreateFile(Extended(path),0x80000000,1,IntPtr.Zero,3,0x00300000,IntPtr.Zero)) {
      if(h.IsInvalid)throw new Win32Exception(Marshal.GetLastWin32Error());
      var before=Inspect(h,path,false);long length=((long)before.SizeHigh<<32)|before.SizeLow;
      if(length<0 || length>maximum)throw new IOException("Context file exceeds the size bound.");
      using(var stream=new FileStream(h,FileAccess.Read,65536,false)) {
        var bytes=new byte[(int)length];int at=0,got;
        while(at<bytes.Length && (got=stream.Read(bytes,at,bytes.Length-at))>0)at+=got;
        if(at!=bytes.Length || stream.ReadByte()!=-1)throw new IOException("Context length changed during read.");
        var after=Inspect(h,path,false);
        if(before.Volume!=after.Volume || before.IdHigh!=after.IdHigh || before.IdLow!=after.IdLow || before.SizeHigh!=after.SizeHigh || before.SizeLow!=after.SizeLow || before.WriteHigh!=after.WriteHigh || before.WriteLow!=after.WriteLow)throw new IOException("Context source changed during read.");
        using(var hash=SHA256.Create())return new ContextReadResult{Bytes=length,Data=bytes,Sha256=BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","").ToLowerInvariant()};
      }
    }
  }
  public static void WriteNew(string path,byte[] bytes) {
    using(var h=CreateFile(Extended(path),0x40000000,0,IntPtr.Zero,1,0x00300000,IntPtr.Zero)) {
      if(h.IsInvalid)throw new Win32Exception(Marshal.GetLastWin32Error());
      Inspect(h,path,false);
      using(var stream=new FileStream(h,FileAccess.Write,65536,false)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
    }
  }
}
'@
}

function Assert-ContextRelative([object]$Value) {
    if($Value -isnot [string] -or !$Value -or $Value.Length -gt 600){throw 'Invalid capsule relative path.'}
    $parts=$Value.Split('/')
    foreach($part in $parts){
        if(!$part -or $part -in @('.','..') -or $part -match '[\\:\x00-\x1f\x7f<>"|?*]' -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)'){throw 'Unsafe capsule relative path.'}
    }
    if($Value -in @('manifest.json','_pull-receipt.json')){throw 'Reserved capsule receipt filename.'}
    if([IO.Path]::GetExtension($Value).ToLowerInvariant() -notin @('.json','.jsonl','.md','.txt','.csv','.html','.png','.jpg','.jpeg')){throw 'Capsule permits metadata and evidence images only.'}
    return ($parts -join '\')
}

function Assert-ContextManifest([object]$Manifest,[string]$Name) {
    if($Manifest.format -cne 'interlinked-context-capsule-v1' -or $Manifest.capsuleName -cne $Name){throw 'Capsule manifest identity differs.'}
    $rows=@($Manifest.files);if(!$rows.Count -or $rows.Count -gt 200){throw 'Capsule file count exceeds200 or is empty.'}
    $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $total=[long]0
    foreach($row in $rows){
        $rel=Assert-ContextRelative $row.relativePath
        if(!$seen.Add($rel)){throw 'Duplicate capsule path.'}
        if(($row.bytes -isnot [int] -and $row.bytes -isnot [long]) -or [long]$row.bytes -lt 0 -or [long]$row.bytes -gt 33554432 -or $row.sha256 -isnot [string] -or $row.sha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Invalid capsule size or hash.'}
        $total+=[long]$row.bytes
    }
    foreach($entry in $seen){
        $prefix=$entry
        while($prefix.Contains('\')){
            $prefix=$prefix.Substring(0,$prefix.LastIndexOf('\'))
            if($seen.Contains($prefix)){throw 'Capsule file/directory prefix collision.'}
        }
    }
    if($total -gt 67108864){throw 'Capsule exceeds64MiB; use the project client for project/media payloads.'}
    return $total
}

function Copy-ContextEntry([string]$Source,[string]$Destination,[object]$Row) {
    $read=[InterlinkedContextReader]::Read($Source,33554432)
    if($read.Bytes -ne [long]$Row.bytes -or $read.Sha256 -cne $Row.sha256){throw 'Source capsule file differs from pinned manifest.'}
    $guard=[InterlinkedContextReader]::Guard((Split-Path -Parent $Destination))
    try {
        [InterlinkedContextReader]::WriteNew($Destination,$read.Data)
        $verify=[InterlinkedContextReader]::Read($Destination,33554432)
        if($verify.Bytes -ne $read.Bytes -or $verify.Sha256 -cne $read.Sha256){throw 'Local capsule readback differs.'}
        return [pscustomobject]@{relativePath=$Row.relativePath;bytes=$read.Bytes;sha256=$read.Sha256;status='COPIED_AND_HASH_VERIFIED'}
    }finally{$guard.Dispose()}
}

function Ensure-ContextParents([string]$Run,[string]$Relative) {
    $parts=$Relative.Split('\');$current=$Run
    for($i=0;$i -lt $parts.Length-1;$i++){
        $held=[InterlinkedContextReader]::Guard($current)
        try{
            $next=Join-Path $current $parts[$i]
            # Create only one leaf below a held ordinary parent. An existing
            # junction is rejected before any descendant can be created.
            [void][IO.Directory]::CreateDirectory($next)
            [InterlinkedContextReader]::Plain($next)
            $probe=[InterlinkedContextReader]::Guard($next);$probe.Dispose()
            $current=$next
        }finally{$held.Dispose()}
    }
}

function Get-ContextCheckout([string]$ScriptDirectory) {
    $directory=$ScriptDirectory.TrimEnd('\')
    $repository=Split-Path -Parent $directory
    if((Split-Path -Leaf $directory) -ine 'handoff' -or (Split-Path -Leaf $repository) -ine 'repository'){throw 'Run the committed repository\handoff helper, not a relocated copy.'}
    return (Split-Path -Parent $repository)
}

if($DefineOnly){return}
$run=$null;$guard=$null;$privateGuard=$null;$runGuard=$null;$rows=New-Object 'System.Collections.Generic.List[object]'
try {
    if($env:COMPUTERNAME -cne 'THIS-MACHINE' -or $env:USERPROFILE -ine 'C:\Users\Jude'){throw 'Run locally in Jude''s THIS-MACHINE session.'}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if($identity.User.Value -cne 'S-1-5-21-1655665044-1352998972-69516628-1001'){throw 'The current Windows account SID differs from the reviewed Jude account.'}
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run normally without administrator mode.'}
    if($CapsuleName -cnotmatch '^INTERLINKED-Codex-Handoff-2026-10-29-[0-9a-f]{32}$' -or $ManifestSha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Supply the exact reviewed capsule name and lowercase manifest SHA256.'}
    $checkout=Get-ContextCheckout $PSScriptRoot
    if($checkout -ine 'C:\Users\Jude\Documents\Codex\INTERLINKED'){throw 'Run from the intended local C:\Users\Jude\Documents\Codex\INTERLINKED checkout.'}
    Initialize-ContextReader
    $guard=[InterlinkedContextReader]::Guard($checkout)
    $gitGuard=[InterlinkedContextReader]::Guard((Join-Path $checkout '.git'));$gitGuard.Dispose()
    $source='\\MSI\ProjectLibrary\Start-Here\'+$CapsuleName
    $manifestRead=[InterlinkedContextReader]::Read(($source+'\manifest.json'),4194304)
    if($manifestRead.Sha256 -cne $ManifestSha256){throw 'Published capsule manifest hash differs.'}
    $manifest=(New-Object Text.UTF8Encoding($false,$true)).GetString($manifestRead.Data).TrimStart([char]0xfeff) | ConvertFrom-Json
    $total=Assert-ContextManifest $manifest $CapsuleName
    $private=Join-Path $checkout '.handoff'
    if(!(Test-Path -LiteralPath $private)){[void][IO.Directory]::CreateDirectory($private)}
    [InterlinkedContextReader]::Plain($private)
    $privateGuard=[InterlinkedContextReader]::Guard($private)
    $run=Join-Path $private ($CapsuleName+'-pull-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
    if(Test-Path -LiteralPath $run){throw 'Fresh capsule destination already exists.'}
    [void][IO.Directory]::CreateDirectory($run);$runGuard=[InterlinkedContextReader]::Guard($run)
    [InterlinkedContextReader]::WriteNew((Join-Path $run 'manifest.json'),$manifestRead.Data)
    if(([InterlinkedContextReader]::Read((Join-Path $run 'manifest.json'),4194304)).Sha256 -cne $ManifestSha256){throw 'Local pinned manifest readback differs.'}
    Write-Host ('Pulling only '+@($manifest.files).Count+' private context files ('+[Math]::Round($total/1MB,2)+' MiB). Project/media caches are untouched.')
    foreach($row in $manifest.files){
        $relative=Assert-ContextRelative $row.relativePath;$target=Join-Path $run $relative
        Ensure-ContextParents $run $relative
        $rows.Add((Copy-ContextEntry ($source+'\'+$relative) $target $row))
    }
    $receipt=[ordered]@{format='interlinked-context-pull-receipt-v1';status='VERIFIED_CONTEXT_SNAPSHOT';utc=[DateTime]::UtcNow.ToString('o');capsuleName=$CapsuleName;manifestSha256=$ManifestSha256;source=$source;destination=$run;count=$rows.Count;bytes=$total;files=$rows.ToArray();projectPayloadFetched=$false;liveKitOrCacheChanged=$false}
    [InterlinkedContextReader]::WriteNew((Join-Path $run '_pull-receipt.json'),(New-Object Text.UTF8Encoding($false)).GetBytes(($receipt | ConvertTo-Json -Depth 8)+"`n"))
    Write-Host ('VERIFIED_CONTEXT_SNAPSHOT: '+$run);exit 0
}catch{
    if($run -and $runGuard){
        try{$failure=[ordered]@{format='interlinked-context-pull-receipt-v1';status='INCOMPLETE_PRESERVED';utc=[DateTime]::UtcNow.ToString('o');capsuleName=$CapsuleName;manifestSha256=$ManifestSha256;error=$_.Exception.Message;files=$rows.ToArray();automaticCleanup=$false};[InterlinkedContextReader]::WriteNew((Join-Path $run '_pull-receipt.json'),(New-Object Text.UTF8Encoding($false)).GetBytes(($failure | ConvertTo-Json -Depth 8)+"`n"))}catch{}
    }
    Write-Host ('Context pull blocked; existing files preserved. '+$_.Exception.Message) -ForegroundColor Red;exit 2
}finally{if($runGuard){$runGuard.Dispose()};if($privateGuard){$privateGuard.Dispose()};if($guard){$guard.Dispose()}}
