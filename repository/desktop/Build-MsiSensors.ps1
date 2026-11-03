# Build only: no remote writes, task registration, driver or publisher startup.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,
      [Parameter(Mandatory=$true)][string]$IdentityReport,
      [Parameter(Mandatory=$true)][string]$VendorDirectory,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$VendorExeSha256,
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$VendorDllSha256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$output=[IO.Path]::GetFullPath($OutputDirectory)
function Ordinary([string]$Path){
 $full=[IO.Path]::GetFullPath($Path);if($full.StartsWith('\\')){throw 'Use ordinary local build paths.'}
 $walk=$full;while($walk){if(Test-Path -LiteralPath $walk){if(([int][IO.File]::GetAttributes($walk)-band 0x441400)-ne 0){throw 'Linked, cloud or offline build path refused.'}};$walk=[IO.Path]::GetDirectoryName($walk)};return $full
}
[void](Ordinary $output)
foreach($forbidden in @('C:\Users\Jude\Documents\MSI-Project-Library','C:\Users\Jude\AppData\Local\ProjectLibrary\Cache','C:\Program Files\INTERLINKED MSI Sensors','C:\ProgramData\INTERLINKED')){
 if($output.Equals($forbidden,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($forbidden+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Build outside installations and managed state.'}
}
if(Test-Path -LiteralPath $output){throw 'Use a new build directory.'}
$identityPath=Ordinary $IdentityReport
if((Get-Item -LiteralPath $identityPath).Length -gt 65536){throw 'Identity report exceeds bounds.'}
$identity=Get-Content -LiteralPath $identityPath -Raw|ConvertFrom-Json
$sid='S-1-5-21-3086848077-1833151167-136462239-1001'
if($identity.computer -cne 'MSI' -or $identity.sid -cne $sid -or @($identity.cpu).Count -ne 1 -or $identity.cpu[0].Manufacturer -cne 'GenuineIntel' -or $identity.cpu[0].Name -notmatch 'i7-10750H'){throw 'Fresh MSI machine, account and CPU evidence required.'}
$observed=if($identity.utc -is [DateTime]){$identity.utc.ToUniversalTime()}else{[DateTime]::Parse([string]$identity.utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()}
$age=([DateTime]::UtcNow-$observed).TotalHours
if($age -lt 0 -or $age -gt 24){throw 'MSI identity report is future-dated or older than one day.'}
$vendor=Ordinary $VendorDirectory
$pins=@{'HWINFO.exe'=$VendorExeSha256;'HWiNFO64.dll'=$VendorDllSha256}
foreach($name in $pins.Keys){$path=Ordinary (Join-Path $vendor $name);if((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -cne $pins[$name]){throw 'Reviewed vendor hash differs.'}}
[void][IO.Directory]::CreateDirectory($output)
$encoding=[Text.UTF8Encoding]::new($false);$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$pinFile=Join-Path $output 'MsiSensorPins.cs'
function Write-Pins([string]$PublisherHash){
 $text='internal static class MsiSensorPins { internal const string UserSid="'+$sid+'", ExeHash="'+$VendorExeSha256+'", DllHash="'+$VendorDllSha256+'", PublisherHash="'+$PublisherHash+'", CpuModelSubstring="i7-10750H", CpuSensorName="CPU Package", CpuGroupSuffix=": DTS"; }'
 [IO.File]::WriteAllText($pinFile,$text,$encoding)
}
Write-Pins ''
$common=Join-Path $PSScriptRoot 'MsiSensorCommon.cs';$publisher=Join-Path $output 'INTERLINKED.MSI.Sensor.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll ('/out:'+$publisher) $pinFile $common (Join-Path $PSScriptRoot 'MsiSensorPublisher.cs') (Join-Path $PSScriptRoot 'MsiSensorNative.cs')
if($LASTEXITCODE -ne 0){throw 'MSI sensor compilation failed.'}
$publisherHash=(Get-FileHash -LiteralPath $publisher).Hash.ToLowerInvariant();Write-Pins $publisherHash
Copy-Item -LiteralPath (Join-Path $vendor 'HWINFO.exe') -Destination (Join-Path $output 'HWINFO.exe')
Copy-Item -LiteralPath (Join-Path $vendor 'HWiNFO64.dll') -Destination (Join-Path $output 'HWiNFO64.dll')
$manifest=Join-Path $output 'setup.manifest'
[IO.File]::WriteAllText($manifest,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="requireAdministrator" uiAccess="false"/></requestedPrivileges></security></trustInfo></assembly>',$encoding)
$setup=Join-Path $output 'INTERLINKED.MSI.Sensors.Setup.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll /reference:Microsoft.CSharp.dll ('/win32manifest:'+$manifest) ('/resource:'+$publisher+',publisher') ('/resource:'+(Join-Path $output 'HWINFO.exe')+',vendor-exe') ('/resource:'+(Join-Path $output 'HWiNFO64.dll')+',vendor-dll') ('/out:'+$setup) $pinFile $common (Join-Path $PSScriptRoot 'MsiSensorSetup.cs')
if($LASTEXITCODE -ne 0){throw 'MSI sensor setup compilation failed.'}
$record=[ordered]@{format='interlinked-msi-sensor-build-v1';machine='MSI';userSid=$sid;cpuModel='Intel Core i7-10750H';cpuSensorName='CPU Package';cpuGroupSuffix=': DTS';identityReportSha256=(Get-FileHash -LiteralPath $identityPath).Hash.ToLowerInvariant();observedAt=[DateTime]::UtcNow.ToString('o');publisherSha256=$publisherHash;setupSha256=(Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant();vendorHashes=$pins;installed=$false;liveCpuMappingVerified=$false}
[IO.File]::WriteAllText((Join-Path $output 'build.json'),($record|ConvertTo-Json -Depth 5),$encoding)
$record|ConvertTo-Json -Depth 5
