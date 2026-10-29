[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Workspace,[string]$StatsData='')
$ErrorActionPreference='Stop'
if ($Workspace.StartsWith('\\') -or -not (Test-Path -LiteralPath $Workspace -PathType Container)) { throw 'Expected a fetched local project folder.' }
if ($StatsData) {
    if ($StatsData.StartsWith('\\') -or -not (Test-Path -LiteralPath $StatsData -PathType Container)) { throw 'Expected a fetched local Stats library.' }
    $env:STATS_DATA_DIR=[IO.Path]::GetFullPath($StatsData)
    Write-Host ('The Stats data folder for this terminal: ' + $env:STATS_DATA_DIR) -ForegroundColor Cyan
}
Set-Location -LiteralPath $Workspace
Write-Host ('Working folder: ' + $Workspace)
Write-Host 'Review this project''s setup instructions before running it. No project code has been started.'
Write-Host 'Close applications and this terminal before Save changes back or Free local space.'
