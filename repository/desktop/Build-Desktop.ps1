[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DependencyRoot,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$VioletAtlas,
    [string]$VioletAnimationPackage
)
$ErrorActionPreference = 'Stop'
$dependency = (Resolve-Path -LiteralPath $DependencyRoot).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
$version = '1.0.4314.35'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw '.NET Framework C# compiler is required.' }
$spec = [xml](Get-Content -LiteralPath (Join-Path $dependency 'Microsoft.Web.WebView2.nuspec') -Raw)
if ($spec.package.metadata.version -cne $version) { throw 'Unexpected WebView2 SDK version.' }
# The official NuGet archive is downloaded separately; no package-manager code runs.
$expectedArchiveSha256 = '90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4'
$archive = Join-Path (Split-Path -Parent $dependency) 'webview2.nupkg'
if (-not (Test-Path -LiteralPath $archive -PathType Leaf) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expectedArchiveSha256) { throw 'Pinned official WebView2 NuGet archive missing or changed.' }
if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) { throw 'Use a fresh empty build output directory.' }
} else { New-Item -ItemType Directory -Path $output | Out-Null }
$core = Join-Path $dependency 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $dependency 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$loader = Join-Path $dependency 'runtimes\win-x64\native\WebView2Loader.dll'
# Confirm extracted dependencies still equal the sealed archive's members.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($relative in @('lib/net462/Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll','LICENSE.txt','NOTICE.txt')) {
        $entry = $zip.GetEntry($relative)
        if ($null -eq $entry) { throw "SDK member missing: $relative" }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $sha = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','').ToLowerInvariant() } finally { $stream.Dispose(); $hasher.Dispose() }
        if ((Get-FileHash -LiteralPath (Join-Path $dependency $relative) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sha) { throw "Extracted SDK member changed: $relative" }
    }
} finally { $zip.Dispose() }
Add-Type -AssemblyName System.Drawing
$iconPath = Join-Path $output 'INTERLINKED-build.ico'
$bitmap = New-Object Drawing.Bitmap 64,64
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$pen = New-Object Drawing.Pen ([Drawing.Color]::FromArgb(98,255,141)),5
try {
    $graphics.Clear([Drawing.Color]::FromArgb(6,14,10))
    $graphics.DrawRectangle($pen,5,5,53,53)
    $graphics.DrawLine($pen,22,16,22,48); $graphics.DrawLine($pen,40,16,40,48)
    $graphics.DrawLine($pen,16,16,46,16); $graphics.DrawLine($pen,16,48,46,48)
    $icon = [Drawing.Icon]::FromHandle($bitmap.GetHicon())
    $iconStream = [IO.File]::Create($iconPath)
    try { $icon.Save($iconStream) } finally { $iconStream.Dispose(); $icon.Dispose() }
} finally { $pen.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
$arguments = @('/nologo','/target:winexe','/platform:x64','/optimize+','/warn:4',('/win32icon:' + $iconPath),('/out:' + (Join-Path $output 'INTERLINKED.exe')),('/reference:' + $core),('/reference:' + $forms),'/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Net.Http.dll','/reference:System.Web.Extensions.dll',(Join-Path $PSScriptRoot 'Interlinked.cs'),(Join-Path $PSScriptRoot 'MasterTray.cs'))
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop compilation failed.' }
$atlas=(Resolve-Path -LiteralPath $VioletAtlas).Path
$atlasImage=[Drawing.Image]::FromFile($atlas)
try {if($atlasImage.Width -ne 1536 -or $atlasImage.Height -ne 2288){throw 'Use the approved v2 Violet atlas.'}}finally{$atlasImage.Dispose()}
$workerArguments=@('/nologo','/target:exe','/platform:x64','/optimize+','/warn:4',('/out:'+(Join-Path $output 'INTERLINKED.Master.Worker.exe')),'/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',(Join-Path $PSScriptRoot 'MasterWorker.cs'))
$animationStream=$null;$animationResource=$null
try {
 if(-not [string]::IsNullOrWhiteSpace($VioletAnimationPackage)){
  $animationResource=Join-Path $output 'INTERLINKED-build-violet.bin'
  & (Join-Path $PSScriptRoot 'New-VioletAnimationBundle.ps1') -PackageRoot $VioletAnimationPackage -OutputPath $animationResource | Out-Null
  $animationStream=[IO.File]::Open($animationResource,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  $workerArguments+=('/resource:'+$animationResource+',Interlinked.VioletAnimations.v1')
 }
 & $compiler @workerArguments
 if($LASTEXITCODE -ne 0){throw 'MASTER worker compilation failed.'}
} finally {if($animationStream){$animationStream.Dispose()};if($animationResource -and (Test-Path -LiteralPath $animationResource)){Remove-Item -LiteralPath $animationResource}}
[IO.File]::WriteAllText((Join-Path $output 'INTERLINKED.Master.Worker.exe.config'),'<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup></configuration>',[Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath $atlas -Destination (Join-Path $output 'Violet.png')
Remove-Item -LiteralPath $iconPath
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'INTERLINKED.exe.config') -Destination $output
Copy-Item -LiteralPath $core,$forms,$loader -Destination $output
Copy-Item -LiteralPath (Join-Path $dependency 'LICENSE.txt') -Destination (Join-Path $output 'WebView2-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $dependency 'NOTICE.txt') -Destination (Join-Path $output 'WebView2-NOTICE.txt')
$files = [ordered]@{}
foreach ($name in @('INTERLINKED.exe','INTERLINKED.exe.config','INTERLINKED.Master.Worker.exe','INTERLINKED.Master.Worker.exe.config','Violet.png','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt','WebView2-NOTICE.txt')) {
    $files[$name] = (Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{ format='interlinked-desktop-checksums-v1'; sdkVersion=$version; sdkArchiveSha256=$expectedArchiveSha256; createdUtc=[DateTime]::UtcNow.ToString('o'); files=$files }
[IO.File]::WriteAllText((Join-Path $output 'DESKTOP-CHECKSUMS.json'),($manifest | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
[pscustomobject]@{Status='DESKTOP_BUILT';OutputDirectory=$output;Files=$files.Count;SdkVersion=$version} | ConvertTo-Json
