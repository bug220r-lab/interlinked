[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,[Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $root){throw 'Use an absent synthetic package fixture directory.'}
if($root.StartsWith('C:\Users\Jude\Documents\MSI-Project-Library',[StringComparison]::OrdinalIgnoreCase) -or $root.StartsWith((Join-Path $env:LOCALAPPDATA 'ProjectLibrary'),[StringComparison]::OrdinalIgnoreCase)){throw 'Fixtures must stay outside production.'}
New-Item -ItemType Directory -Path $root | Out-Null
Add-Type -AssemblyName System.Drawing
$required=@('INTERLINKED.exe','INTERLINKED.exe.config','INTERLINKED.Master.Worker.exe','INTERLINKED.Master.Worker.exe.config','Violet.png','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt','WebView2-NOTICE.txt')
$checks=[Collections.Generic.List[string]]::new()
function Seal([string]$package){
 $files=[ordered]@{}
 foreach($name in $required){$files[$name]=(Get-FileHash -LiteralPath (Join-Path $package $name) -Algorithm SHA256).Hash.ToLowerInvariant()}
 [IO.File]::WriteAllText((Join-Path $package 'DESKTOP-CHECKSUMS.json'),([ordered]@{format='interlinked-desktop-checksums-v1';files=$files}|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
}
function New-Package([string]$name){
 $package=Join-Path $root $name;New-Item -ItemType Directory -Path $package | Out-Null
 foreach($member in $required){if($member -ne 'Violet.png'){[IO.File]::WriteAllText((Join-Path $package $member),'NONEXECUTABLE SYNTHETIC FIXTURE '+$member)}}
 $image=[Drawing.Bitmap]::new(1536,2288)
 try{$image.Save((Join-Path $package 'Violet.png'),[Drawing.Imaging.ImageFormat]::Png)}finally{$image.Dispose()}
 Seal $package
 return $package
}
function Preflight([string]$package){& (Join-Path $PSScriptRoot 'Test-Desktop.ps1') -PackageDirectory $package -Python $Python -FixtureDirectory (Join-Path $root ('never-created-'+[Guid]::NewGuid().ToString('N'))) -PreflightOnly}
function Reject([string]$package,[string]$label){
 $rejected=$false;try{Preflight $package | Out-Null}catch{$rejected=$true}
 if(-not $rejected){throw ('Expected refusal: '+$label)};$checks.Add($label)
}
$valid=New-Package 'valid'
$result=Preflight $valid | ConvertFrom-Json
if($result.Status -ne 'PACKAGE_PREFLIGHT_PASS' -or $result.Files -ne 10){throw 'Valid exact package preflight failed.'}
$checks.Add('Exact ten-file package with v2 atlas passes without starting synthetic executable bytes')
if(@(Get-ChildItem -LiteralPath $root -Directory | Where-Object {$_.Name.StartsWith('never-created-')}).Count -ne 0){throw 'Preflight created an execution fixture.'}
$checks.Add('Preflight creates no runtime or desktop fixture')
$tampered=New-Package 'tampered';[IO.File]::AppendAllText((Join-Path $tampered 'INTERLINKED.Master.Worker.exe'),'changed');Reject $tampered 'Changed worker bytes are refused before executable launch'
$missing=New-Package 'missing-seal-member';$manifest=Get-Content (Join-Path $missing 'DESKTOP-CHECKSUMS.json') -Raw|ConvertFrom-Json;$manifest.files.PSObject.Properties.Remove('INTERLINKED.Master.Worker.exe');$manifest|ConvertTo-Json -Depth 5|Set-Content (Join-Path $missing 'DESKTOP-CHECKSUMS.json');Reject $missing 'A seal omitting the MASTER worker cannot pass as an old desktop package'
$extra=New-Package 'extra';[IO.File]::WriteAllText((Join-Path $extra 'unexpected.dll'),'fixture');Reject $extra 'An unsealed extra package file is refused'
$traversal=New-Package 'traversal';$manifest=Get-Content (Join-Path $traversal 'DESKTOP-CHECKSUMS.json') -Raw|ConvertFrom-Json;$manifest.files|Add-Member -NotePropertyName '../outside.exe' -NotePropertyValue ('a'*64);$manifest|ConvertTo-Json -Depth 5|Set-Content (Join-Path $traversal 'DESKTOP-CHECKSUMS.json');Reject $traversal 'A path-traversal package member is refused'
$invalidHash=New-Package 'invalid-hash';$manifest=Get-Content (Join-Path $invalidHash 'DESKTOP-CHECKSUMS.json') -Raw|ConvertFrom-Json;$manifest.files.'INTERLINKED.exe'='not-a-hash';$manifest|ConvertTo-Json -Depth 5|Set-Content (Join-Path $invalidHash 'DESKTOP-CHECKSUMS.json');Reject $invalidHash 'Malformed package hashes are refused'
$wrongAtlas=New-Package 'wrong-atlas';$image=[Drawing.Bitmap]::new(1,1);try{$image.Save((Join-Path $wrongAtlas 'Violet.png'),[Drawing.Imaging.ImageFormat]::Png)}finally{$image.Dispose()};Seal $wrongAtlas;Reject $wrongAtlas 'A hash-valid but wrong-size Violet atlas is refused'
[pscustomobject]@{Status='PASS';Count=$checks.Count;Checks=$checks;Scope='Nonexecuting synthetic package fixtures; no production files, device or app startup'}|ConvertTo-Json -Depth 5
