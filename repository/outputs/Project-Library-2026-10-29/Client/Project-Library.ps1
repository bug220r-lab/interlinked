[CmdletBinding()]
param()

$script:taskClientRoot = $PSScriptRoot
$script:taskPython = Join-Path $PSScriptRoot 'Runtime\python.exe'
$script:taskEngine = Join-Path $PSScriptRoot 'project_library.py'
$script:taskSettingsPath = Join-Path $PSScriptRoot 'Settings.json'

function Quote-NativeArgument([string]$Value) {
    if ($Value -notmatch '[\s"]' -and $Value.Length -gt 0) { return $Value }
    $taskQuoted = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $taskQuoted = [regex]::Replace($taskQuoted, '(\\+)$', '$1$1')
    return '"' + $taskQuoted + '"'
}

function Get-LibrarySettings {
    $taskValue = Get-Content -LiteralPath $script:taskSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskValue.server -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$') { throw 'Invalid MSI computer name/address in Settings.json.' }
    $taskCache = [string]$taskValue.cacheRoot
    if ([string]::IsNullOrWhiteSpace($taskCache)) { $taskCache = Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache' }
    if ($taskCache -notmatch '^[A-Za-z]:\\.+' -or $taskCache -match '[<>"|?*]') { throw 'Cache location must be a local folder such as D:\ProjectCache.' }
    $taskCache = [IO.Path]::GetFullPath($taskCache).TrimEnd('\')
    return [pscustomobject]@{Server=[string]$taskValue.server;Library=('\\' + $taskValue.server + '\ProjectLibrary');Returns=('\\' + $taskValue.server + '\ProjectReturns');Cache=$taskCache}
}

function Test-HelperIntegrity {
    $taskManifest = Get-Content -LiteralPath (Join-Path $script:taskClientRoot 'HELPER-CHECKSUMS.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskManifest.format -ne 'project-library-client-checksums-v1') { throw 'Unrecognized helper integrity manifest.' }
    foreach ($taskEntry in $taskManifest.files) {
        if ($taskEntry.path -match '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:)' ) { throw 'Unsafe helper integrity path.' }
        $taskPath = Join-Path $script:taskClientRoot $taskEntry.path
        if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) { throw ('Missing helper file: ' + $taskEntry.path) }
        if ((Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash -ne $taskEntry.sha256) { throw ('Helper file changed: ' + $taskEntry.path + '. Recopy the published helper from the MSI.') }
    }
}

function Invoke-LibraryEngine([string[]]$EngineArguments) {
    $taskSettings = Get-LibrarySettings
    $taskArgs = @('-B','-X','utf8',$script:taskEngine,'--library',$taskSettings.Library,'--returns',$taskSettings.Returns,'--cache',$taskSettings.Cache) + $EngineArguments
    $taskInfo = New-Object System.Diagnostics.ProcessStartInfo
    $taskInfo.FileName = $script:taskPython
    $taskInfo.Arguments = (($taskArgs | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.RedirectStandardOutput = $true
    $taskInfo.RedirectStandardError = $true
    $taskInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $taskInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $taskInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
    $taskInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $taskProcess = New-Object System.Diagnostics.Process
    $taskProcess.StartInfo = $taskInfo
    $taskErrors = New-Object System.Text.StringBuilder
    $taskStarted = $false
    try {
        if (-not $taskProcess.Start()) { throw 'Could not start the project helper.' }
        $taskStarted = $true
        $taskOutputTask = $taskProcess.StandardOutput.ReadToEndAsync()
        $taskErrorTask = $taskProcess.StandardError.ReadLineAsync()
        while (-not $taskProcess.HasExited) {
            while ($null -ne $taskErrorTask -and $taskErrorTask.IsCompleted) {
                $taskLine = $taskErrorTask.Result
                if ($null -eq $taskLine) { $taskErrorTask = $null; break }
                [void]$taskErrors.AppendLine($taskLine)
                Write-Host $taskLine
                $taskErrorTask = $taskProcess.StandardError.ReadLineAsync()
            }
            Start-Sleep -Milliseconds 150
        }
        $taskProcess.WaitForExit()
        while ($null -ne $taskErrorTask) {
            $taskLine = $taskErrorTask.Result
            if ($null -eq $taskLine) { break }
            [void]$taskErrors.AppendLine($taskLine)
            Write-Host $taskLine
            $taskErrorTask = $taskProcess.StandardError.ReadLineAsync()
        }
        $taskRaw = $taskOutputTask.Result
        $taskExitCode = $taskProcess.ExitCode
        $taskLogRoot = Join-Path $script:taskClientRoot 'Logs'
        [IO.Directory]::CreateDirectory($taskLogRoot) | Out-Null
        $taskLogStem = Join-Path $taskLogRoot ((Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $EngineArguments[0])
        [IO.File]::WriteAllText(($taskLogStem + '.json'),$taskRaw,(New-Object Text.UTF8Encoding($false)))
        [IO.File]::WriteAllText(($taskLogStem + '.log'),$taskErrors.ToString(),(New-Object Text.UTF8Encoding($false)))
        $taskResult = $null
        if (-not [string]::IsNullOrWhiteSpace($taskRaw)) {
            try { $taskResult = $taskRaw | ConvertFrom-Json }
            catch { throw ('Unexpected helper response. See ' + $taskLogStem + '.json') }
        }
        if ($taskExitCode -ne 0) {
            if ($null -ne $taskResult) { Write-Host ($taskResult | ConvertTo-Json -Depth 8) -ForegroundColor Yellow }
            throw ('The operation did not complete (code ' + $taskExitCode + '). Read the message above. Logs: ' + $taskLogRoot)
        }
        return $taskResult
    }
    finally {
        if ($taskStarted) {
            try { if (-not $taskProcess.HasExited) { $taskProcess.Kill(); $taskProcess.WaitForExit() } } catch {}
        }
        $taskProcess.Dispose()
    }
}

function Show-LibraryResult($Result) {
    Write-Host ($Result | ConvertTo-Json -Depth 7)
}

function Resolve-ProjectPath($Project,[string]$Relative) {
    if ($Project.id -notmatch '^[a-z0-9-]+$') { throw 'Invalid project ID.' }
    if ($Relative -notmatch '^Files([/\\]|$)' -or $Relative -match '(^|[/\\])\.\.?([/\\]|$)|:|[<>"|?*]') { throw 'Invalid project workspace path.' }
    $taskSettings = Get-LibrarySettings
    $taskBase = [IO.Path]::GetFullPath((Join-Path $taskSettings.Cache $Project.id)).TrimEnd('\')
    $taskResolved = [IO.Path]::GetFullPath((Join-Path $taskBase $Relative))
    if (-not $taskResolved.StartsWith(($taskBase + '\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Project path escaped its cache.' }
    return $taskResolved
}

function Show-ProjectMenu($Project) {
    while ($true) {
        $taskSettings = Get-LibrarySettings
        Write-Host ''
        Write-Host $Project.name -ForegroundColor Cyan
        Write-Host ('Project ID: ' + $Project.id)
        if ($null -ne $Project.bytes) { Write-Host ('Initial selection: {0:N2} GiB' -f ([double]$Project.bytes / 1GB)) }
        if ($Project.description) { Write-Host $Project.description }
        foreach ($taskWarning in @($Project.warnings)) { if ($taskWarning) { Write-Host ('Note: ' + $taskWarning) -ForegroundColor Yellow } }
        foreach ($taskNote in @($Project.notes)) { if ($taskNote) { Write-Host $taskNote } }
        Write-Host '1 Fetch project   2 Open local files   3 Status'
        Write-Host '4 Save changes back   5 Verify saved copy   6 Free local space'
        Write-Host '7 Open a configured project terminal'
        Write-Host '0 Back to project list'
        $taskChoice = Read-Host 'Choose'
        try {
            switch ($taskChoice) {
                '1' { Show-LibraryResult (Invoke-LibraryEngine @('fetch',[string]$Project.id)) }
                '2' {
                    $taskRelative = if ($Project.workspaceRelative) { [string]$Project.workspaceRelative } else { 'Files' }
                    $taskPath = Resolve-ProjectPath $Project $taskRelative
                    if (-not (Test-Path -LiteralPath $taskPath -PathType Container)) { throw 'Fetch this project first.' }
                    Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $taskPath + '"') | Out-Null
                }
                '3' { Show-LibraryResult (Invoke-LibraryEngine @('status',[string]$Project.id)) }
                '4' {
                    Write-Host 'Close the project in its editing application before saving back.'
                    Show-LibraryResult (Invoke-LibraryEngine @('save',[string]$Project.id))
                }
                '5' { Show-LibraryResult (Invoke-LibraryEngine @('verify',[string]$Project.id)) }
                '6' {
                    Write-Host 'Close all applications using this project.' -ForegroundColor Yellow
                    Write-Host 'This removes only this managed local copy, after checking its saved remote version and all local files.'
                    $taskConfirm = Read-Host ('Type the project ID to free its local space: ' + $Project.id)
                    if ($taskConfirm -ceq $Project.id) { Show-LibraryResult (Invoke-LibraryEngine @('release',[string]$Project.id,'--confirm',[string]$Project.id)) }
                    else { Write-Host 'No files removed.' }
                }
                '7' {
                    $taskRelative = if ($Project.workspaceRelative) { [string]$Project.workspaceRelative } else { 'Files' }
                    $taskPath = Resolve-ProjectPath $Project $taskRelative
                    if (-not (Test-Path -LiteralPath $taskPath -PathType Container)) { throw 'Fetch this project first.' }
                    $taskTerminalArgs = @('-NoLogo','-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File',(Join-Path $script:taskClientRoot 'Open-ProjectConsole.ps1'),'-Workspace',$taskPath)
                    if ($Project.environmentHints -and $Project.environmentHints.STATS_DATA_DIR) {
                        $taskDataPath = Resolve-ProjectPath $Project ([string]$Project.environmentHints.STATS_DATA_DIR.relativeToWorkingSet)
                        $taskTerminalArgs += @('-StatsData',$taskDataPath)
                    }
                    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList (($taskTerminalArgs | ForEach-Object { Quote-NativeArgument $_ }) -join ' ') | Out-Null
                }
                '0' { return }
                default { Write-Host 'Choose one of the listed numbers.' }
            }
        }
        catch { Write-Host $_.Exception.Message -ForegroundColor Red }
    }
}

function Start-ProjectLibrary {
    $ErrorActionPreference = 'Stop'
    if ($script:taskClientRoot.StartsWith('\\')) { throw 'Copy or extract the helper to a local folder on the NEW PC before running it.' }
    $taskPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run Project-Library.cmd normally in your signed-in account, without administrator mode.' }
    Test-HelperIntegrity
    Write-Host 'MSI Project Library - fetch, save back, and free local space' -ForegroundColor Cyan
    Write-Host 'Keep the MSI powered on and connected. Use one active working copy of each project.'
    while ($true) {
        try {
            $taskSettings = Get-LibrarySettings
            Write-Host ''
            Write-Host ('Library: ' + $taskSettings.Library)
            Write-Host ('Local cache: ' + $taskSettings.Cache)
            Write-Host '1 Choose a project   2 Open MSI network share   3 Change cache folder   0 Exit'
            $taskChoice = Read-Host 'Choose'
            if ($taskChoice -eq '0') { return }
            if ($taskChoice -eq '2') {
                Write-Host 'If Windows asks, use the OLD MSI account and its password. A Windows Hello PIN is not a network password.'
                Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $taskSettings.Library + '"') | Out-Null
                continue
            }
            if ($taskChoice -eq '3') {
                Write-Host 'Changing this does not move or remove any existing cached projects.'
                $taskNewCache = Read-Host 'New local cache folder (blank cancels)'
                if (-not [string]::IsNullOrWhiteSpace($taskNewCache)) {
                    if ($taskNewCache -notmatch '^[A-Za-z]:\\.+' -or $taskNewCache -match '[<>"|?*]') { throw 'Enter a full local folder path, such as D:\ProjectCache.' }
                    $taskConfig = Get-Content -LiteralPath $script:taskSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    $taskConfig.cacheRoot = [IO.Path]::GetFullPath($taskNewCache).TrimEnd('\')
                    [IO.File]::WriteAllText($script:taskSettingsPath,($taskConfig | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
                }
                continue
            }
            if ($taskChoice -ne '1') { continue }
            $taskList = Invoke-LibraryEngine @('list')
            $taskAll = @($taskList.projects)
            $taskSearch = Read-Host 'Search project name (blank lists the first 60)'
            $taskMatches = @($taskAll | Where-Object { [string]::IsNullOrWhiteSpace($taskSearch) -or $_.name.IndexOf($taskSearch,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or $_.id.IndexOf($taskSearch,[StringComparison]::OrdinalIgnoreCase) -ge 0 })
            $taskShown = @($taskMatches | Select-Object -First 60)
            for ($taskIndex=0; $taskIndex -lt $taskShown.Count; $taskIndex++) {
                $taskP = $taskShown[$taskIndex]
                Write-Host ('{0,3}. {1}  ({2:N2} GiB)' -f ($taskIndex+1),$taskP.name,([double]$taskP.bytes/1GB))
            }
            if ($taskMatches.Count -gt 60) { Write-Host ('Showing 60 of ' + $taskMatches.Count + '. Go back and use a more specific search to see others.') }
            if ($taskShown.Count -eq 0) { Write-Host 'No matching projects.'; continue }
            $taskPick = Read-Host 'Project number (blank cancels)'
            $taskNumber = 0
            if ([int]::TryParse($taskPick,[ref]$taskNumber) -and $taskNumber -ge 1 -and $taskNumber -le $taskShown.Count) { Show-ProjectMenu $taskShown[$taskNumber-1] }
        }
        catch { Write-Host $_.Exception.Message -ForegroundColor Red; Write-Host 'Use option 2 to open the share and sign in through Windows if access failed.' }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-ProjectLibrary; exit 0 }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
