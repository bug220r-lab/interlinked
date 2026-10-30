[CmdletBinding()]
param(
    [string]$IndexPath,
    [string]$IndexSha256,
    [switch]$DefineOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Initialize-DefinitionReader {
    if ('InterlinkedDefinitionReader' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
public sealed class DefinitionReadResult {
  public long Bytes; public string Sha256; public byte[] Data;
}
public static class InterlinkedDefinitionReader {
  [StructLayout(LayoutKind.Sequential)] struct Info {
    public uint Attr, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh;
    public uint Volume, SizeHigh, SizeLow, Links, IdHigh, IdLow;
  }
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern SafeFileHandle CreateFile(string p,uint access,uint share,IntPtr sa,uint mode,uint flags,IntPtr template);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern uint GetFileAttributes(string p);
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern bool GetFileInformationByHandle(SafeFileHandle h,out Info info);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder p,uint count,uint flags);
  const uint Bad=0x400|0x1000|0x40000|0x400000;
  static string Extended(string p) { return @"\\?\"+p; }
  public static uint Attributes(string p) {
    uint a=GetFileAttributes(Extended(p));
    if(a==0xffffffff) throw new Win32Exception(Marshal.GetLastWin32Error());
    return a;
  }
  public static void Plain(string p) {
    if(p.Length<3 || !Char.IsLetter(p[0]) || p[1]!=':' || p[2]!='\\') throw new IOException("Local drive path required.");
    string current=p.Substring(0,3); uint a=Attributes(current);
    if((a&Bad)!=0) throw new IOException("Reparse/cloud ancestor refused.");
    foreach(string component in p.Substring(3).Split('\\')) {
      if(component.Length==0 || component=="." || component=="..") throw new IOException("Noncanonical path refused.");
      current=current.TrimEnd('\\')+"\\"+component; a=Attributes(current);
      if((a&Bad)!=0) throw new IOException("Reparse/cloud path refused.");
    }
  }
  public static DefinitionReadResult Read(string p,long maximum,bool keepBytes) {
    Plain(p);
    using(SafeFileHandle h=CreateFile(Extended(p),0x80000000,1,IntPtr.Zero,3,0x00300000,IntPtr.Zero)) {
      if(h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
      Info before,after;
      if(!GetFileInformationByHandle(h,out before)) throw new Win32Exception(Marshal.GetLastWin32Error());
      if((before.Attr&(Bad|0x10))!=0) throw new IOException("Ordinary resident file required.");
      var final=new StringBuilder(32768); uint n=GetFinalPathNameByHandle(h,final,(uint)final.Capacity,0);
      if(n==0 || n>=final.Capacity || !String.Equals(final.ToString(),Extended(p),StringComparison.OrdinalIgnoreCase)) throw new IOException("Opened path resolves elsewhere; read refused.");
      long length=((long)before.SizeHigh<<32)|before.SizeLow;
      if(length<0 || length>maximum) throw new IOException("File exceeds bounded comparison size.");
      using(var stream=new FileStream(h,FileAccess.Read,1048576,false))
      using(var hash=SHA256.Create()) {
        byte[] data=null; byte[] digest;
        if(keepBytes) {
          data=new byte[(int)length];int offset=0,got;
          while(offset<data.Length && (got=stream.Read(data,offset,data.Length-offset))>0) offset+=got;
          if(offset!=data.Length || stream.ReadByte()!=-1) throw new IOException("Metadata length changed.");
          digest=hash.ComputeHash(data);
        } else { digest=hash.ComputeHash(stream); }
        if(!GetFileInformationByHandle(h,out after)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if(before.Volume!=after.Volume || before.IdHigh!=after.IdHigh || before.IdLow!=after.IdLow || before.SizeHigh!=after.SizeHigh || before.SizeLow!=after.SizeLow || before.WriteHigh!=after.WriteHigh || before.WriteLow!=after.WriteLow || stream.Position!=length) throw new IOException("File changed while readlocked.");
        return new DefinitionReadResult {Bytes=length,Sha256=BitConverter.ToString(digest).Replace("-","").ToLowerInvariant(),Data=data};
      }
    }
  }
}
'@
}

function Assert-DefinitionRelative([object]$Value) {
    if ($Value -isnot [string] -or $Value.Length -gt 2048 -or $Value.Length -eq 0) { throw 'Invalid definition relative path.' }
    $parts=$Value.Split('/')
    foreach($part in $parts) {
        if (!$part -or $part -in @('.','..') -or $part -match '[\\:\x00-\x1f\x7f<>"|?*]' -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)') { throw 'Unsafe definition relative path.' }
    }
    return ($parts -join '\')
}

function Get-DefinitionComparison([string]$Target,[object]$Row) {
    try {
        $read=[InterlinkedDefinitionReader]::Read($Target,33554432,$false)
        $status=if($read.Bytes -eq [long]$Row.bytes -and $read.Sha256 -ceq [string]$Row.sha256){'MATCH'}else{'DIFFERENT'}
        return [pscustomobject]@{kind=$Row.kind;targetRelativePath=$Row.targetRelativePath;status=$status;expectedBytes=[long]$Row.bytes;actualBytes=$read.Bytes;expectedSha256=$Row.sha256;actualSha256=$read.Sha256;category=$Row.category}
    } catch {
        $errorValue=$_.Exception
        while($errorValue.InnerException){$errorValue=$errorValue.InnerException}
        $missing=($errorValue -is [ComponentModel.Win32Exception]) -and $errorValue.NativeErrorCode -in @(2,3)
        return [pscustomobject]@{kind=$Row.kind;targetRelativePath=$Row.targetRelativePath;status=$(if($missing){'MISSING'}else{'READ_ERROR'});expectedBytes=[long]$Row.bytes;expectedSha256=$Row.sha256;category=$Row.category;error=$errorValue.Message}
    }
}

function Write-NewDefinitionJson([string]$Path,[object]$Value) {
    $bytes=(New-Object Text.UTF8Encoding($false)).GetBytes(($Value | ConvertTo-Json -Depth 10)+"`n")
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

if($DefineOnly){return}
try {
    if($env:COMPUTERNAME -cne 'THIS-MACHINE' -or $env:USERPROFILE -ine 'C:\Users\Jude'){throw 'Run locally on THIS-MACHINE in Jude''s Windows session.'}
    if(!$IndexPath -or $IndexSha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Supply the reviewed local index path and its lowercase SHA256.'}
    if($IndexPath.StartsWith('\\') -or $IndexPath -notmatch '^[A-Za-z]:\\'){throw 'Index must be a local drive path; UNC/device paths are refused.'}
    $IndexPath=[IO.Path]::GetFullPath($IndexPath)
    Initialize-DefinitionReader
    $read=[InterlinkedDefinitionReader]::Read($IndexPath,8388608,$true)
    if($read.Sha256 -cne $IndexSha256){throw 'Verification index SHA256 differs; no comparison started.'}
    $index=(New-Object Text.UTF8Encoding($false,$true)).GetString($read.Data) | ConvertFrom-Json
    if($index.format -cne 'interlinked-magix-preset-definition-index-v1' -or $index.expectedAppVersion -cne '20.0.1.80' -or $index.status -cne 'SOURCE_HASHES_VERIFIED' -or $index.verificationOnly -ne $true){throw 'Unexpected or incomplete source definition index.'}
    $app='C:\Program Files (x86)\Steam\steamapps\common\MAGIX\Video deluxe 2021 Plus Steam Edition'
    $roots=@{steam_content=($app+'\_Content');roaming_preset='C:\Users\Jude\AppData\Roaming\MAGIX\Videodeluxe27_plus_steam'}
    [InterlinkedDefinitionReader]::Plain($app+'\Videodeluxe.exe')
    $version=[Diagnostics.FileVersionInfo]::GetVersionInfo($app+'\Videodeluxe.exe')
    if(('{0}.{1}.{2}.{3}' -f $version.FileMajorPart,$version.FileMinorPart,$version.FileBuildPart,$version.FilePrivatePart) -cne '20.0.1.80'){throw 'Installed MAGIX version differs from the source index.'}
    $files=@($index.files);if($files.Count -eq 0 -or $files.Count -gt 10000){throw 'Definition count is out of bounds.'}
    $total=[long]0;$seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach($row in $files){
        if($row.kind -isnot [string] -or !$roots.ContainsKey($row.kind)){throw 'Unknown definition kind.'}
        $rel=Assert-DefinitionRelative $row.targetRelativePath
        if($row.kind -ceq 'roaming_preset' -and !$rel.StartsWith('Presets\',[StringComparison]::OrdinalIgnoreCase)){throw 'Roaming comparison is restricted to Presets.'}
        if(!$seen.Add($row.kind+'/'+$rel)){throw 'Duplicate definition destination.'}
        if($row.bytes -isnot [ValueType] -or [long]$row.bytes -lt 0 -or [long]$row.bytes -gt 33554432 -or $row.sha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Invalid definition size or SHA256.'}
        $total+=[long]$row.bytes
    }
    if($total -gt 262144000){throw 'Definition bytes exceed the250MiB comparison bound.'}
    $parent=Split-Path -Parent $IndexPath;[InterlinkedDefinitionReader]::Plain($parent)
    $run=Join-Path $parent ('MAGIX-Definition-Comparison-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[guid]::NewGuid().ToString('N'))
    if(Test-Path -LiteralPath $run){throw 'Comparison output already exists.'}
    [void][IO.Directory]::CreateDirectory($run);[InterlinkedDefinitionReader]::Plain($run)
    $rows=New-Object 'System.Collections.Generic.List[object]';$clock=[Diagnostics.Stopwatch]::StartNew()
    Write-Host ('Comparing '+$files.Count+' definitions ('+[Math]::Round($total/1MB,2)+' MiB). No preset files will be changed.')
    foreach($row in $files){
        $rel=Assert-DefinitionRelative $row.targetRelativePath
        $rows.Add((Get-DefinitionComparison ($roots[$row.kind]+'\'+$rel) $row))
        if($clock.Elapsed.TotalSeconds -ge 5){Write-Host ('Compared '+$rows.Count+'/'+$files.Count+' definitions...');$clock.Restart()}
    }
    $counts=@{MATCH=0;MISSING=0;DIFFERENT=0;READ_ERROR=0};foreach($row in $rows){$counts[$row.status]++}
    $status=if($counts.READ_ERROR){'COMPARISON_INCOMPLETE_READ_ERRORS'}elseif($counts.MISSING -or $counts.DIFFERENT){'COMPARED_DIFFERENCES_RETAINED'}else{'ALL_INDEXED_DEFINITIONS_MATCH'}
    $report=[ordered]@{format='interlinked-magix-preset-definition-comparison-v1';createdUtc=[DateTime]::UtcNow.ToString('o');computer=$env:COMPUTERNAME;userSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;status=$status;indexSha256=$IndexSha256;count=$rows.Count;counts=$counts;sourceDefinitionBytes=$total;readOnly=$true;presetChanges=0;limitations=@('Only the indexed small definitions were compared. Media, thumbnails and75 MIS bundles remain metadata-only.','Differences are retained and reported. This helper never copies, installs or replaces presets.');files=$rows.ToArray()}
    $reportPath=Join-Path $run 'comparison.json';Write-NewDefinitionJson $reportPath $report
    $hash=(Get-FileHash -LiteralPath $reportPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-NewDefinitionJson (Join-Path $run 'receipt.json') ([ordered]@{status=$status;comparisonSha256=$hash;comparisonPath=$reportPath;counts=$counts})
    Write-Host ($status+': '+$reportPath);Write-Host ('Matched '+$counts.MATCH+'; missing '+$counts.MISSING+'; different '+$counts.DIFFERENT+'; read errors '+$counts.READ_ERROR)
    if($counts.READ_ERROR){exit 2};exit 0
} catch {Write-Error ('Definition comparison blocked: '+$_.Exception.Message);exit 2}
