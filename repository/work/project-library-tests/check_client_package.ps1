$ErrorActionPreference = 'Stop'
$taskClientRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\outputs\Project-Library-2026-10-29\Client'))
$taskScriptCount = 0
foreach ($taskScript in @('Project-Library.ps1','Open-ProjectConsole.ps1')) {
    $taskTokens = $null
    $taskErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $taskClientRoot $taskScript),[ref]$taskTokens,[ref]$taskErrors) | Out-Null
    if (@($taskErrors).Count) { throw ($taskErrors | Out-String) }
    $taskScriptCount++
}
. (Join-Path $taskClientRoot 'Project-Library.ps1')
Test-HelperIntegrity
$taskResult = [ordered]@{PowerShellVersion=$PSVersionTable.PSVersion.ToString();ParsedScripts=$taskScriptCount;Integrity='PASS';NetworkAccessed=$false}
$taskJson = $taskResult | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'client-powershell-check.json'),$taskJson,[Text.UTF8Encoding]::new($false))
$taskJson
