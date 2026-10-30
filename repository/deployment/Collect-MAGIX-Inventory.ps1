[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$kit = Split-Path -Parent $PSCommandPath
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N')
$out = Join-Path $kit ('MAGIX-Preset-Inventory-' + $stamp)
if (Test-Path -LiteralPath $out) { throw 'Inventory destination already exists.' }
[void][IO.Directory]::CreateDirectory($out)
$app = 'Video deluxe 2021 Plus Steam Edition'
$edition = 'Videodeluxe27_plus_steam'
$steam = Join-Path ${env:ProgramFiles(x86)} ('Steam\steamapps\common\MAGIX\' + $app)
$roots = @(
    @{id='steam_content';path=(Join-Path $steam '_Content')},
    @{id='programdata_magix';path=(Join-Path $env:ProgramData 'MAGIX')},
    @{id='public_magix';path=(Join-Path $env:PUBLIC 'Documents\MAGIX')},
    @{id='roaming_edition';path=(Join-Path $env:APPDATA ('MAGIX\' + $edition))},
    @{id='documents_magix';path=(Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'MAGIX')}
)
$rows = New-Object 'System.Collections.Generic.List[object]'
$issues = New-Object 'System.Collections.Generic.List[object]'
$rootInfo = New-Object 'System.Collections.Generic.List[object]'
$maxItems = 50000
$seen = 0
foreach ($root in $roots) {
    $rootPath = [IO.Path]::GetFullPath($root.path)
    try { $rootItem = Get-Item -LiteralPath $rootPath -Force -ErrorAction Stop }
    catch { $rootInfo.Add([pscustomobject]@{id=$root.id;path=$rootPath;status='unavailable';error=$_.Exception.Message}); continue }
    $rootInfo.Add([pscustomobject]@{id=$root.id;path=$rootPath;status='enumerated'})
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($rootPath)
    while ($stack.Count -gt 0) {
        $folder = $stack.Pop()
        try {
            $item = Get-Item -LiteralPath $folder -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                $issues.Add([pscustomobject]@{path=$folder;status='reparse_directory_not_followed'}); continue
            }
            $children = @(Get-ChildItem -LiteralPath $folder -Force -ErrorAction Stop)
        } catch { $issues.Add([pscustomobject]@{path=$folder;status='enumeration_error';error=$_.Exception.Message}); continue }
        foreach ($child in $children) {
            $seen++
            if ($seen -gt $maxItems) { throw 'Inventory exceeded 50000 items; partial report is not claimed complete.' }
            if ($child.PSIsContainer) { $stack.Push($child.FullName); continue }
            $relative = $child.FullName.Substring($rootPath.TrimEnd('\').Length + 1)
            $rows.Add([pscustomobject]@{
                root=$root.id;relativePath=$relative;bytes=$child.Length;
                modifiedUtc=$child.LastWriteTimeUtc.ToString('o');attributes=[int64]$child.Attributes
            })
        }
    }
}
$fonts = New-Object 'System.Collections.Generic.List[object]'
foreach ($regPath in @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts','HKCU:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')) {
    try {
        $key = Get-Item -LiteralPath $regPath -ErrorAction Stop
        foreach ($name in $key.GetValueNames()) {
            $fonts.Add([pscustomobject]@{scope=$regPath.Substring(0,4);name=$name;file=[string]$key.GetValue($name)})
        }
    } catch { $issues.Add([pscustomobject]@{path=$regPath;status='font_registry_unavailable';error=$_.Exception.Message}) }
}
$exe = Join-Path $steam 'Videodeluxe.exe'
$version = $null
if (Test-Path -LiteralPath $exe) { $version=(Get-Item -LiteralPath $exe).VersionInfo.FileVersion }
$report = [ordered]@{
    format='interlinked-magix-preset-font-inventory-v1';createdUtc=(Get-Date).ToUniversalTime().ToString('o');
    computer=$env:COMPUTERNAME;user=$env:USERNAME;application=$exe;version=$version;
    metadataOnly=$true;roots=@($rootInfo.ToArray());files=@($rows.ToArray());
    installedFontRegistrations=@($fonts.ToArray());issues=@($issues.ToArray())
}
$json = $report | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $out 'inventory.json'), $json, (New-Object Text.UTF8Encoding($false)))
$hash = (Get-FileHash -LiteralPath (Join-Path $out 'inventory.json') -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $out 'inventory.sha256.txt'), $hash + "`r`n", (New-Object Text.UTF8Encoding($false)))
Write-Host ('INVENTORY_READY: ' + $out) -ForegroundColor Green
Write-Host ('Files listed: ' + $rows.Count + '; registered fonts: ' + $fonts.Count + '; coverage notes: ' + $issues.Count)
Write-Host 'No presets, project files, settings, or fonts were changed.'
