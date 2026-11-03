[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [Parameter(Mandatory=$true)][string]$ManifestSha256,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$PythonSha256,
    [string]$BindAddress='127.0.0.1',
    [int]$Port=47443,
    [switch]$StartAtLogin
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=[IO.Path]::GetFullPath($InstallDirectory)
if($root.StartsWith('\\') -or -not (Test-Path -LiteralPath $root -PathType Container)){throw 'Use an existing ordinary local package directory.'}
$ancestor=Get-Item -LiteralPath $root
while($ancestor){if(([long]$ancestor.Attributes -band 0x441400) -ne 0){throw 'Linked/offline package ancestors are refused.'};$ancestor=$ancestor.Parent}
foreach($item in @(Get-Item -LiteralPath $root)+@(Get-ChildItem -LiteralPath $root -Recurse -Force)){
    if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Linked node packages are refused.'}
}
$manifestPath=Join-Path $root 'NODE-CHECKSUMS.json'
if((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ine $ManifestSha256){throw 'Node package manifest differs.'}
$manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$members=@('interlinked_ai_node.py','interlinked_ai_workspaces.py','interlinked_stats_process.py','interlinked_configuration.py','Initialize-Node.ps1','Start-Node.ps1','Set-NodeNetwork.ps1','Set-TransportRuntime.ps1','README.md')
if($manifest.format -ceq 'interlinked-ai-node-package-v2'){$members+=@('interlinked_history_import.py','interlinked_history_store.py','interlinked_history_service.py','interlinked_history_runtime.py','interlinked_history_gateway.py','Migrate-Node.ps1')}
elseif($manifest.format -cne 'interlinked-ai-node-package-v1'){throw 'Node package format differs.'}
if((@($manifest.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'files|format' -or (@($manifest.files.path | Sort-Object) -join '|') -cne (($members | Sort-Object) -join '|')){throw 'Node package member set differs.'}
foreach($entry in $manifest.files){if((@($entry.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'path|sha256' -or $entry.sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid node package member metadata.'}}
foreach($entry in $manifest.files){
    if($entry.path -notmatch '^[A-Za-z0-9_.-]+$'){throw 'Unsafe package member.'}
    if((Get-FileHash -LiteralPath (Join-Path $root $entry.path) -Algorithm SHA256).Hash -ine $entry.sha256){throw 'Node package member differs.'}
}
$runtime=(Resolve-Path -LiteralPath $Python).Path
if((Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash -ine $PythonSha256){throw 'Reviewed Python executable differs.'}
$address=$null
if(-not [Net.IPAddress]::TryParse($BindAddress,[ref]$address)){throw 'BindAddress must be an explicit local interface address.'}
if($BindAddress -eq '0.0.0.0' -or $BindAddress -eq '::' -or $Port -lt 1024 -or $Port -gt 65535){throw 'Choose a specific interface and unprivileged port.'}
if($BindAddress -ne '127.0.0.1' -and -not (Get-NetIPAddress -IPAddress $BindAddress -ErrorAction SilentlyContinue)){throw 'The selected address is not assigned to this computer.'}
$config=Join-Path $root 'node.json'
if(Test-Path -LiteralPath $config){throw 'An initialized node already exists. Preserve it; replacement requires a reviewed package migration.'}
$secrets=Join-Path $root 'Secrets'
$state=Join-Path $root 'State'
if((Test-Path -LiteralPath $secrets) -or (Test-Path -LiteralPath $state)){throw 'Existing unowned node state is preserved.'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true,$false)
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')))
Set-Acl -LiteralPath $root -AclObject $acl
[IO.Directory]::CreateDirectory($secrets) | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
public static class InterlinkedNodeCertificate {
 static byte[] Length(int n) { if(n<128)return new byte[]{(byte)n}; var v=new System.Collections.Generic.List<byte>(); while(n>0){v.Insert(0,(byte)(n&255));n>>=8;}v.Insert(0,(byte)(128|v.Count));return v.ToArray(); }
 static byte[] TLV(byte tag,byte[] data){using(var s=new MemoryStream()){s.WriteByte(tag);var l=Length(data.Length);s.Write(l,0,l.Length);s.Write(data,0,data.Length);return s.ToArray();}}
 static byte[] Integer(byte[] bytes){int i=0;while(i<bytes.Length-1&&bytes[i]==0)i++;using(var s=new MemoryStream()){if(bytes[i]>=128)s.WriteByte(0);s.Write(bytes,i,bytes.Length-i);return TLV(2,s.ToArray());}}
 static string Pem(string label,byte[] data){return "-----BEGIN "+label+"-----\n"+Convert.ToBase64String(data,Base64FormattingOptions.InsertLineBreaks)+"\n-----END "+label+"-----\n";}
 public static string Create(string computer,string certificate,string key){
  using(var rsa=new RSACng(3072)){var request=new CertificateRequest("CN="+computer,rsa,HashAlgorithmName.SHA256,RSASignaturePadding.Pkcs1);
   request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false,false,0,true));
   request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature|X509KeyUsageFlags.KeyEncipherment,true));
   using(var cert=request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5),DateTimeOffset.UtcNow.AddYears(2))){
    byte[] der=cert.Export(X509ContentType.Cert);File.WriteAllText(certificate,Pem("CERTIFICATE",der),new UTF8Encoding(false));
    var p=rsa.ExportParameters(true);using(var s=new MemoryStream()){foreach(var v in new byte[][]{new byte[]{0},p.Modulus,p.Exponent,p.D,p.P,p.Q,p.DP,p.DQ,p.InverseQ}){var b=Integer(v);s.Write(b,0,b.Length);}File.WriteAllText(key,Pem("RSA PRIVATE KEY",TLV(48,s.ToArray())),new UTF8Encoding(false));}
    using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(der)).Replace("-","").ToLowerInvariant();
   }
  }
 }
}
'@
$certificate=Join-Path $secrets 'node-cert.pem'
$key=Join-Path $secrets 'node-key.pem'
$fingerprint=[InterlinkedNodeCertificate]::Create($env:COMPUTERNAME,$certificate,$key)
$record=[ordered]@{format='interlinked-ai-node-install-v1';computer=$env:COMPUTERNAME;sid=$sid.Value;python=$runtime;pythonSha256=$PythonSha256.ToLowerInvariant();manifestSha256=$ManifestSha256.ToLowerInvariant();bind=$BindAddress;port=$Port;fingerprint=$fingerprint;startup=$false}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $config -Encoding UTF8
if($StartAtLogin){
    $taskName='INTERLINKED AI Node'
    if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Existing startup task is preserved. Node initialized without changing it.'}
    $launcher=Join-Path $root 'Start-Node.ps1'
    $args='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$launcher+'" -InstallDirectory "'+$root+'"'
    $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $args
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $sid.Value
    $principal=New-ScheduledTaskPrincipal -UserId $sid.Value -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    $record.startup=$true
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $config -Encoding UTF8
}
Write-Output ('Node initialized for '+$record.computer+' / '+$record.sid)
Write-Output ('Certificate SHA256: '+$fingerprint)
Write-Output 'Start-Node.ps1 -Pair displays a five-minute pairing code. No model runtime was started or changed.'
