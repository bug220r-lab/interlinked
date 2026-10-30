[CmdletBinding()]
param(
    [ValidateSet('Inspect','Apply')][string]$Mode = 'Inspect',
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestSha256,
    [switch]$Elevated
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$expectedSid = 'S-1-5-21-1655665044-1352998972-69516628-1001'
$python = 'C:\Users\Jude\Documents\MSI-Project-Library\Project-Library-Client\Runtime\python.exe'
$importer = Join-Path $PSScriptRoot 'import_magix_presets.py'
# Filled from the reviewed source at package sealing; never accepts a manifest-provided executable.
$expectedImporterSha = 'd1714a746432d560c9732633fa8ba042fc94f9634210e98b896872ec8085ef44'
$clientRoot = Split-Path (Split-Path $python -Parent) -Parent
$runtimePins = @{
    'Runtime\LICENSE.txt' = '886a0ead2d89030ee62dbff52b04e47ab91998341295bb9c56fb952b4e081c7a'
    'Runtime\python.exe' = '5640e9be2fd33d05a1a9ae1228e2354609a69ab831605995ec4e2bd6da8fff5e'
    'Runtime\python3.dll' = '62845ba25fcd76557c127b01b9dc6680845f8a4acb97fadf72035d7a6eb2d2d3'
    'Runtime\python312._pth' = '8e4ac3e5a2b8f58484e4135c66323717ac7fd2fc3b74b44a96a1a74992a66c89'
    'Runtime\python312.dll' = '886fa6b89e09ca6c87599c802402fdca160e06b2fa9c658f6fe91ed926f63e1f'
    'Runtime\python312.zip' = '4788a5b9aee9f7bd60778685ff9e9192c25069c98e7f8f9268b7565423feedb8'
    'Runtime\vcruntime140_1.dll' = '1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f'
    'Runtime\vcruntime140.dll' = 'd5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066'
    'Runtime\DLLs\_asyncio.pyd' = '6d86fefd411bac347ac52d2bb06379b06d47cd8ac3e81d425b8559d53343e197'
    'Runtime\DLLs\_bz2.pyd' = '538d21186927914039c42fe2473aaf052abadbde9fc373883c5d848aecc8a62c'
    'Runtime\DLLs\_ctypes_test.pyd' = '114ea0011af223aa4fafe504387c12796512e08fc6bc12a75713aca047174b4e'
    'Runtime\DLLs\_ctypes.pyd' = '94f5e1801f982c86c1e3121d359f198b303a555668d60a42618e6e94d7d3e580'
    'Runtime\DLLs\_decimal.pyd' = '8e2abfa85936c606d5f08accf5072823eb452308db4dae2f8bf41eab16c7fbfb'
    'Runtime\DLLs\_elementtree.pyd' = '0fcc9c1e05d47bf2c5b492e42220ba668f9097c249042cb4260e25b808801367'
    'Runtime\DLLs\_hashlib.pyd' = '00f94f34de2637c87dba0209ef22d728e90fa519ae46bde39df60df725dff496'
    'Runtime\DLLs\_lzma.pyd' = '85a41379ef09827ae55f28fd567be791fa50fce35a69dbb5a71288b2d817d424'
    'Runtime\DLLs\_msi.pyd' = '4d3c66d89a6780d2155bc23488e659153207cc24507bff8271022f7089a5ded9'
    'Runtime\DLLs\_multiprocessing.pyd' = '180212b6055a51950e2169e7468713f7a12cb6d406550265ab0db2e93f377fef'
    'Runtime\DLLs\_overlapped.pyd' = '8be3fbd1e1de1e5af58db5b15bf657085b8d01aee3e071fdb39cdf30aa756742'
    'Runtime\DLLs\_queue.pyd' = '0ab4148d0f53e4257d9a4f2d18da151167e90694ae3cdb4809b5a53979cabed6'
    'Runtime\DLLs\_socket.pyd' = '0313f76c51107429703e1de6512f66f748a61a65d7f01c063d13c44ebf8660d0'
    'Runtime\DLLs\_sqlite3.pyd' = 'bcdc1a6da767c541d11737be8317e308bc5d37a09219d5e00e12e7cf2b504765'
    'Runtime\DLLs\_ssl.pyd' = 'a524c08819e4e4e6b186e8686a6a091f17616a599b3f825bde60e05919f097fb'
    'Runtime\DLLs\_testbuffer.pyd' = '2996bfff982d9c78b638622979b10411032c3aed4df3766e37f0dafa2b113ef7'
    'Runtime\DLLs\_testcapi.pyd' = '9498e53d179053ee7890f5c78f6c1d313b8e9266af5937a11455cf9abdb5dd36'
    'Runtime\DLLs\_testclinic.pyd' = 'ebbff32787706fa79075bae23a572a158b50f197bb03ed4ab8258e08b2df5bdd'
    'Runtime\DLLs\_testconsole.pyd' = 'f56bd20c5c48002fb8995f7bba0cfd52f2336ea26d58e3d69978883574e15450'
    'Runtime\DLLs\_testimportmultiple.pyd' = 'd669c06a6e3b241d130dd4247887a0a55fca3153e2f4d3e9028e76098ee954bb'
    'Runtime\DLLs\_testinternalcapi.pyd' = '32c1789d03f4d2065066913ac55e0bfb67839bd62e6c817ef5f6c67dd724cd13'
    'Runtime\DLLs\_testmultiphase.pyd' = 'c3a3bae4f901715d80a85c8aa58c7de086125d4457dfe06184321429a87bc3e6'
    'Runtime\DLLs\_testsinglephase.pyd' = '7dfd3ffe8187ae124a7b6b262b1bc5d45f75539d8d08882adfe4cfba08bc258d'
    'Runtime\DLLs\_tkinter.pyd' = '5b5ca94841d231e965cd7ce0ee25747fad4297d852bfc4ccfa508ada0f5981f1'
    'Runtime\DLLs\_uuid.pyd' = 'f1b6cf9efa0169ce68854766965ec4ccf27a465df27ebc788a873e533364f342'
    'Runtime\DLLs\_wmi.pyd' = 'fac8e2ce1e839ee0f94fb8a77f6a7b518f1d00d84fd930c6b0d3156c219501f8'
    'Runtime\DLLs\_zoneinfo.pyd' = '27bef453957e5a015d14e886b9e49fea74e0a8be52d2e04750e4d84a581eeed2'
    'Runtime\DLLs\libcrypto-3-x64.dll' = '587c56108d7af19b02ff8fa785c9df703af66a0e8e2bd66031870ff4000bb6a9'
    'Runtime\DLLs\libffi-8.dll' = 'f5fbc815520d987431124b1d2a79f687d8166a1b6cec205e03f9a9d51ea3d380'
    'Runtime\DLLs\libssl-3-x64.dll' = 'f77484cd42cfcee649abd12867d0a26b1bf5c171c1ac80a0aa7c1083c2e58e10'
    'Runtime\DLLs\pyexpat.pyd' = '9d31f81b7ae59851794c496b920dde678b12b812748aa7570ebe4a50c9d10ea0'
    'Runtime\DLLs\select.pyd' = '0fe0f9261b247f8405250c8ad5e2a0b42e88205fc8b095224b796fa69bc14d95'
    'Runtime\DLLs\sqlite3.dll' = 'ee6af52d64d99036df3b94f1c233dfef3d29732bb783d74470daf42a074366af'
    'Runtime\DLLs\tcl86t.dll' = 'fbfd065f861ec0a90dd513bc209c56bbc23c54d2839964a0ec2df95848af7860'
    'Runtime\DLLs\tk86t.dll' = 'cd2f60075064dfc2e65c88b239a970cb4bd07cb3eec7cc26fb1bf978d4356b08'
    'Runtime\DLLs\unicodedata.pyd' = '06c8c710869f7ab725f1e47dd2b7d6578fa865eab97aa1210a423380f2cc4c74'
    'Runtime\DLLs\winsound.pyd' = '50b20a35d02901c2519d45a12cf67e322e7506ba2bd2e95345e91304fff0e51c'
    'project_library.py' = '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d'
}
$heldFiles = New-Object 'System.Collections.Generic.List[System.IDisposable]'
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($env:COMPUTERNAME -ine 'THIS-MACHINE' -or $id.User.Value -ne $expectedSid) { throw 'Run this helper on THIS-MACHINE as Jude.' }
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    $admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($Mode -eq 'Apply' -and -not $admin) {
        if ($Elevated) { throw 'Elevation did not retain the expected Jude account.' }
        $escaped = $PSCommandPath.Replace("'", "''")
        $code = "& '$escaped' -Mode Apply -ManifestSha256 '$ManifestSha256' -Elevated; exit `$LASTEXITCODE"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        $process = Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -WindowStyle Hidden -Verb RunAs -Wait -PassThru -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
        if ($process.ExitCode -eq 0) { Write-Host 'Import verified. Keep the Import-Result receipt in this package. Reopen MAGIX to check your titles and effects.' }
        else { Write-Host 'Import stopped. Keep any Import-Failure receipt in this package for review. Existing files were not overwritten.' }
        exit $process.ExitCode
    }
    $runtime = Split-Path $python -Parent
    $actualRuntimeFiles = @(Get-ChildItem -LiteralPath $runtime -Recurse -File -Force)
    if ($actualRuntimeFiles.Count -ne ($runtimePins.Count - 1)) { throw 'Unexpected runtime file set.' }
    foreach ($entry in $runtimePins.GetEnumerator()) {
        $path = Join-Path $clientRoot $entry.Key
        $cursor = $path
        while ($null -ne $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (([int]$item.Attributes -band 0x441400) -ne 0) { throw "Link/cloud runtime path refused: $cursor" }
            $parent = [IO.Directory]::GetParent($cursor)
            $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
        }
        $stream = [IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $heldFiles.Add($stream)
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $actual = ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','').ToLowerInvariant() } finally { $hash.Dispose() }
        if ($actual -ne $entry.Value) { throw "Sealed runtime/client hash mismatch: $($entry.Key)" }
    }
    foreach ($path in @($python,$importer)) {
        $cursor = $path
        while ($null -ne $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse path refused: $($item.FullName)" }
            $parent = [IO.Directory]::GetParent($cursor)
            $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
        }
    }
    # Keep importer bytes stable while Python loads and executes them.
    $held = [IO.File]::Open($importer,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = ([BitConverter]::ToString($sha.ComputeHash($held))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
        if ($actual -ne $expectedImporterSha) { throw 'Importer source hash differs from the reviewed package.' }
        $arguments = @('-I','-B',$importer,'--manifest-sha256',$ManifestSha256)
        if ($Mode -eq 'Apply') { $arguments += '--apply' }
        & $python @arguments
        $code = $LASTEXITCODE
    } finally { $held.Dispose() }
    if ($code -eq 0) { Write-Host 'Finished. Keep the Import-Result/Import-Receipt file. Reopen MAGIX to check the imported titles and effects.' }
    else { Write-Host 'Stopped. Existing files were not overwritten. Keep the failure receipt for review.' }
    exit $code
} catch {
    Write-Error $_
    exit 1
} finally {
    foreach ($stream in $heldFiles) { $stream.Dispose() }
}
