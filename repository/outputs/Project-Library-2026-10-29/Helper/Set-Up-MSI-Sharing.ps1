#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Restore, [switch]$PauseWhenDone)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$script:ExpectedComputer = 'MSI'
$script:ExpectedAccount = 'MSI\jlewi'
$script:ExpectedSid = 'S-1-5-21-3086848077-1833151167-136462239-1001'
$script:LibraryRoot = 'C:\Users\jlewi\Documents\Codex\2026-10-28\prep\outputs\Project-Library-2026-10-29'
$script:HelperRoot = Join-Path $script:LibraryRoot 'Helper'
$script:StatePath = Join-Path $script:HelperRoot 'sharing-ownership.json'
$script:State = $null
$script:CreatedThisRun = $false
$script:ExitCode = 1

function Assert-PlainPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ne $Path -or $full.StartsWith('\\')) { throw ('Expected an exact local absolute path: ' + $Path) }
    $chain = New-Object 'System.Collections.Generic.List[string]'
    $current = $full
    while ($current) {
        $chain.Add($current)
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
    for ($i = $chain.Count - 1; $i -ge 0; $i--) {
        if (Test-Path -LiteralPath $chain[$i]) {
            $item = Get-Item -LiteralPath $chain[$i] -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw ('Reparse path refused: ' + $chain[$i]) }
        }
    }
}

function Write-AtomicJson([string]$Path, $Value) {
    Assert-PlainPath $Path
    $temp = $Path + '.new-' + [guid]::NewGuid().ToString('N')
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 14) + "`n")
    $stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temp, $Path, [NullString]::Value) } else { [IO.File]::Move($temp, $Path) }
}

function Save-State {
    $script:State.UpdatedUtc = (Get-Date).ToUniversalTime().ToString('o')
    Write-AtomicJson $script:StatePath $script:State
}

function Get-AccountSid([string]$Name) {
    if ($Name -match '^S-1-') { return ([Security.Principal.SecurityIdentifier]::new($Name)).Value }
    return ([Security.Principal.NTAccount]::new($Name)).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Find-Share([string]$Name) {
    try { Get-SmbShare -Name $Name -ErrorAction Stop }
    catch { if ($_.CategoryInfo.Category -ne 'ObjectNotFound') { throw } }
}

function Find-Rule([string]$Name) {
    try { Get-NetFirewallRule -Name $Name -ErrorAction Stop }
    catch { if ($_.CategoryInfo.Category -ne 'ObjectNotFound') { throw } }
}

function Assert-ShareDefinition($Share, $Spec) {
    if ($Share.Name -ne $Spec.Name -or [IO.Path]::GetFullPath($Share.Path) -ne $Spec.Path -or $Share.Description -ne $Spec.Description) { throw ('Share collision or ownership drift: ' + $Spec.Name) }
    if (-not $Share.EncryptData -or $Share.CachingMode.ToString() -ne 'None') { throw ('Share security settings changed: ' + $Spec.Name) }
    $access = @(Get-SmbShareAccess -Name $Spec.Name -ErrorAction Stop)
    if ($access.Count -ne 1 -or $access[0].AccessControlType.ToString() -ne 'Allow' -or $access[0].AccessRight.ToString() -ne $Spec.Right -or (Get-AccountSid $access[0].AccountName) -ne $script:ExpectedSid) { throw ('Share ACL is not the expected single named-user permission: ' + $Spec.Name) }
}

function Assert-RuleDefinition($Rule) {
    $spec = $script:State.Firewall
    if ($Rule.Name -ne $spec.Name -or $Rule.Description -ne $spec.Description -or $Rule.Direction.ToString() -ne 'Inbound' -or $Rule.Action.ToString() -ne 'Allow' -or $Rule.Enabled.ToString() -ne 'True' -or $Rule.Profile.ToString() -ne 'Private') { throw 'Firewall rule ownership/settings drift; refusing replacement or removal.' }
    $port = $Rule | Get-NetFirewallPortFilter
    $address = $Rule | Get-NetFirewallAddressFilter
    $app = $Rule | Get-NetFirewallApplicationFilter
    if ($port.Protocol.ToString() -notin @('TCP','6') -or (@($port.LocalPort) -join ',') -ne '445' -or (@($address.RemoteAddress) -join ',') -ne 'LocalSubnet' -or $app.Program -ne 'System') { throw 'Firewall filter drift; refusing replacement or removal.' }
}

function Get-ServiceEvidence {
    $service = Get-CimInstance -ClassName Win32_Service -Filter "Name='LanmanServer'"
    if ($null -eq $service) { throw 'Windows LanmanServer service was not found.' }
    return [pscustomobject]@{ Name=$service.Name; State=$service.State; StartMode=$service.StartMode }
}

function Assert-State($Data) {
    if ($Data.Format -ne 'msi-project-library-sharing-v1' -or $Data.Computer -ne $script:ExpectedComputer -or $Data.AccountSid -ne $script:ExpectedSid -or $Data.LibraryRoot -ne $script:LibraryRoot) { throw 'Sharing ownership receipt does not match this helper.' }
    $owner = [guid]::Parse($Data.OwnerId).ToString('N')
    $description = 'Codex Project Library 2026-10-29 owner ' + $owner
    $expected = @{'ProjectLibrary'=@((Join-Path $script:LibraryRoot 'HostLibrary'),'Read'); 'ProjectReturns'=@((Join-Path $script:LibraryRoot 'Returns'),'Change')}
    if (@($Data.Shares).Count -ne 2) { throw 'Unexpected share receipt count.' }
    $seen = @{}
    foreach ($spec in $Data.Shares) {
        if (-not $expected.ContainsKey($spec.Name) -or $seen.ContainsKey($spec.Name) -or $spec.Path -ne $expected[$spec.Name][0] -or $spec.Right -ne $expected[$spec.Name][1] -or $spec.Description -ne $description) { throw 'Unexpected share ownership receipt.' }
        $seen[$spec.Name]=$true; Assert-PlainPath $spec.Path
    }
    if ($Data.Firewall.Name -ne ('CodexProjectLibrary-20261029-' + $owner) -or $Data.Firewall.Description -ne $description) { throw 'Unexpected firewall ownership receipt.' }
    if ($Data.Service.PriorState -notin @('Stopped','Running') -or $Data.Service.PriorStartMode -notin @('Auto','Manual','Disabled')) { throw 'Unexpected original service state.' }
}

function Restore-OwnedResources {
    $problems = New-Object 'System.Collections.Generic.List[string]'
    foreach ($spec in $script:State.Shares) {
        if (-not $spec.CreationIntent) { continue }
        try {
            $share = Find-Share $spec.Name
            if ($share) { Assert-ShareDefinition $share $spec; Remove-SmbShare -Name $spec.Name -Force -Confirm:$false; if (Find-Share $spec.Name) { throw ('Share removal was not confirmed: ' + $spec.Name) } }
            $spec.Created=$false
            Save-State
        } catch { $problems.Add($_.Exception.Message) }
    }
    if ($script:State.Firewall.CreationIntent) {
        try {
            $rule = Find-Rule $script:State.Firewall.Name
            if ($rule) { Assert-RuleDefinition $rule; $rule | Remove-NetFirewallRule -Confirm:$false; if (Find-Rule $script:State.Firewall.Name) { throw 'Firewall rule removal was not confirmed.' } }
            $script:State.Firewall.Created=$false
            Save-State
        } catch { $problems.Add($_.Exception.Message) }
    }
    if ($script:State.Service.StartIntent -or $script:State.Service.StartupChangeIntent) {
        try {
            $now = Get-ServiceEvidence
            if ($script:State.Service.PriorState -eq 'Stopped' -and $script:State.Service.StartIntent -and $now.State -ne 'Stopped') {
                if ($problems.Count) { throw 'Service restore deferred because a share or firewall resource could not be restored.' }
                $sessions = @(Get-SmbSession -ErrorAction Stop)
                $otherShares = @(Get-SmbShare | Where-Object { -not $_.Special })
                if ($sessions.Count -or $otherShares.Count) { throw 'Service restore deferred: active SMB sessions or other non-system shares exist. They were preserved.' }
                Stop-Service -Name LanmanServer -ErrorAction Stop
                (Get-Service LanmanServer).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20))
            }
            if ($script:State.Service.StartupChangeIntent) {
                $now = Get-ServiceEvidence
                if ($now.StartMode -ne 'Manual' -and $now.StartMode -ne $script:State.Service.PriorStartMode) { throw 'Service startup mode changed outside this helper; preserving current mode.' }
                if ($script:State.Service.PriorStartMode -eq 'Disabled') { Set-Service -Name LanmanServer -StartupType Disabled }
            }
            $script:State.Service.StartedByHelper=$false
            $script:State.Service.StartupChanged=$false
            Save-State
        } catch { $problems.Add($_.Exception.Message) }
    }
    $script:State.RestoreProblems=@($problems.ToArray())
    $script:State.Status=$(if ($problems.Count) { 'RESTORE_PARTIAL_REVIEW_REQUIRED' } else { 'RESTORED_OWNED_RESOURCES' })
    Save-State
    if ($problems.Count) { throw ($problems.ToArray() -join "`n") }
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($env:COMPUTERNAME -ne $script:ExpectedComputer -or $identity.User.Value -ne $script:ExpectedSid) { throw 'Run this helper elevated as the existing MSI\jlewi account on MSI. It does not create accounts.' }
    if ((Get-AccountSid $script:ExpectedAccount) -ne $script:ExpectedSid) { throw 'The expected Windows account identity has changed.' }
    Assert-PlainPath $script:HelperRoot
    Assert-PlainPath $script:StatePath
    if (-not (Test-Path -LiteralPath $script:HelperRoot -PathType Container)) { throw 'The pinned helper folder is missing.' }
    # Exclusive local handle prevents two setup/restore instances from changing the same resources.
    $lockPath = Join-Path $script:HelperRoot '.sharing-setup.lock'
    Assert-PlainPath $lockPath
    $script:Lock = [IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    if (Test-Path -LiteralPath $script:StatePath) {
        $script:State = Get-Content -LiteralPath $script:StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert-State $script:State
    } else {
        if ($Restore) { throw 'No ownership receipt exists; no shares or rules will be removed.' }
        $prior = Get-ServiceEvidence
        if ($prior.State -notin @('Stopped','Running')) { throw 'The Server service is transitioning or paused; inspect it before setup.' }
        $owner = [guid]::NewGuid().ToString('N')
        $description = 'Codex Project Library 2026-10-29 owner ' + $owner
        $script:State = [pscustomobject]@{
            Format='msi-project-library-sharing-v1'; Computer=$script:ExpectedComputer; Account=$script:ExpectedAccount; AccountSid=$script:ExpectedSid; LibraryRoot=$script:LibraryRoot; OwnerId=$owner
            Status='PLANNED'; CreatedUtc=(Get-Date).ToUniversalTime().ToString('o'); UpdatedUtc=$null; RestoreProblems=@(); LastError=$null
            Shares=@(
                [pscustomobject]@{Name='ProjectLibrary';Path=(Join-Path $script:LibraryRoot 'HostLibrary');Right='Read';Description=$description;CreationIntent=$false;Created=$false},
                [pscustomobject]@{Name='ProjectReturns';Path=(Join-Path $script:LibraryRoot 'Returns');Right='Change';Description=$description;CreationIntent=$false;Created=$false})
            Firewall=[pscustomobject]@{Name=('CodexProjectLibrary-20261029-'+$owner);Description=$description;CreationIntent=$false;Created=$false}
            Service=[pscustomobject]@{PriorState=$prior.State;PriorStartMode=$prior.StartMode;StartIntent=$false;StartedByHelper=$false;StartupChangeIntent=$false;StartupChanged=$false}
        }
        Save-State
    }
    if ($Restore) {
        Restore-OwnedResources
        Write-Host 'Only the recorded library shares/rule and eligible helper-started service changes were restored. Library and returned files were retained.' -ForegroundColor Green
    } else {
        if ($script:State.Status -eq 'RESTORED_OWNED_RESOURCES') { throw 'This setup receipt has been restored. Ask for a reviewed new setup receipt before enabling again.' }
        $profiles = @(Get-NetConnectionProfile -ErrorAction Stop)
        if (-not @($profiles | Where-Object { $_.NetworkCategory.ToString() -eq 'Private' }).Count) { throw 'No Private network is active. Set only your trusted network to Private manually, then rerun. This helper does not change network profiles.' }
        # Preflight share collisions before creating any share or firewall rule.
        foreach ($spec in $script:State.Shares) {
            Assert-PlainPath $spec.Path
            $existing = Find-Share $spec.Name
            if ($existing) {
                if (-not $spec.CreationIntent) { throw ('Existing unowned share refused: ' + $spec.Name) }
                Assert-ShareDefinition $existing $spec
            }
        }
        $existingRule = Find-Rule $script:State.Firewall.Name
        if ($existingRule) {
            if (-not $script:State.Firewall.CreationIntent) { throw 'An unowned firewall rule uses this name.' }
            Assert-RuleDefinition $existingRule
        }
        $service = Get-ServiceEvidence
        if ($service.State -eq 'Stopped') {
            if ($service.StartMode -eq 'Disabled') {
                $script:State.Service.StartupChangeIntent=$true; Save-State; $script:CreatedThisRun=$true
                Set-Service -Name LanmanServer -StartupType Manual
                $script:State.Service.StartupChanged=$true; Save-State
            }
            $script:State.Service.StartIntent=$true; Save-State; $script:CreatedThisRun=$true
            Start-Service -Name LanmanServer
            (Get-Service LanmanServer).WaitForStatus('Running',[TimeSpan]::FromSeconds(20))
            $script:State.Service.StartedByHelper=$true; Save-State
        } elseif ($service.State -ne 'Running') { throw 'The Server service is not in a stable state.' }
        foreach ($spec in $script:State.Shares) {
            if (-not (Test-Path -LiteralPath $spec.Path)) { New-Item -ItemType Directory -Path $spec.Path | Out-Null }
            Assert-PlainPath $spec.Path
            if (-not (Test-Path -LiteralPath $spec.Path -PathType Container)) { throw ('Share root is not a folder: ' + $spec.Path) }
            $existing = Find-Share $spec.Name
            if (-not $existing) {
                $spec.CreationIntent=$true; Save-State; $script:CreatedThisRun=$true
                $parameters = @{Name=$spec.Name;Path=$spec.Path;Description=$spec.Description;EncryptData=$true;CachingMode='None';FolderEnumerationMode='AccessBased';ErrorAction='Stop'}
                if ($spec.Right -eq 'Read') { $parameters.ReadAccess=@($script:ExpectedAccount) } else { $parameters.ChangeAccess=@($script:ExpectedAccount) }
                New-SmbShare @parameters | Out-Null
                $spec.Created=$true; Save-State
            }
            Assert-ShareDefinition (Get-SmbShare -Name $spec.Name) $spec
        }
        if (-not (Find-Rule $script:State.Firewall.Name)) {
            $script:State.Firewall.CreationIntent=$true; Save-State; $script:CreatedThisRun=$true
            New-NetFirewallRule -Name $script:State.Firewall.Name -DisplayName 'MSI Project Library - private local network' -Description $script:State.Firewall.Description -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort 445 -RemoteAddress LocalSubnet -Program System | Out-Null
            $script:State.Firewall.Created=$true; Save-State
        }
        Assert-RuleDefinition (Get-NetFirewallRule -Name $script:State.Firewall.Name)
        $script:State.Status='CONFIGURED_READBACK_VERIFIED'; $script:State.LastError=$null; Save-State
        Write-Host '\\MSI\ProjectLibrary is read-only; \\MSI\ProjectReturns accepts returned copies for MSI\jlewi.' -ForegroundColor Green
        Write-Host 'Use the old MSI account password on the client. A Windows Hello PIN is not the network password. Do not send passwords to chat.'
    }
    $readback = [ordered]@{Status=$script:State.Status;Computer=$env:COMPUTERNAME;AccountSid=$identity.User.Value;Service=(Get-ServiceEvidence);Profiles=@(Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory);Shares=@();Firewall=@();RootAcl=@();CompletedUtc=(Get-Date).ToUniversalTime().ToString('o')}
    foreach ($spec in $script:State.Shares) {
        $found=Find-Share $spec.Name
        if ($found) { $readback.Shares += [ordered]@{Name=$found.Name;Path=$found.Path;EncryptData=$found.EncryptData;Description=$found.Description;Access=@(Get-SmbShareAccess -Name $spec.Name | Select-Object AccountName,AccessControlType,AccessRight)} }
        if (Test-Path -LiteralPath $spec.Path) { $acl=Get-Acl -LiteralPath $spec.Path; $readback.RootAcl += [ordered]@{Path=$spec.Path;Owner=$acl.Owner;Sddl=$acl.Sddl} }
    }
    $rule=Find-Rule $script:State.Firewall.Name
    if ($rule) { $readback.Firewall=@([ordered]@{Name=$rule.Name;Description=$rule.Description;Profile=$rule.Profile.ToString();Enabled=$rule.Enabled.ToString();Direction=$rule.Direction.ToString();Action=$rule.Action.ToString();Ports=@($rule | Get-NetFirewallPortFilter | Select-Object Protocol,LocalPort);Addresses=@($rule | Get-NetFirewallAddressFilter | Select-Object LocalAddress,RemoteAddress);Application=@($rule | Get-NetFirewallApplicationFilter | Select-Object Program)}) }
    $receipt = Join-Path $script:HelperRoot ('sharing-result-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.json')
    Write-AtomicJson $receipt $readback
    Write-Host ('Readback receipt: ' + $receipt)
    $script:ExitCode=0
} catch {
    $message=$_.Exception.Message
    Write-Host $message -ForegroundColor Red
    if ($null -ne $script:State) {
        $script:State.LastError=$message
        try { Save-State } catch { Write-Host ('Could not save failure state: ' + $_.Exception.Message) -ForegroundColor Red }
        if (-not $Restore -and $script:CreatedThisRun) {
            try { Restore-OwnedResources; Write-Host 'Setup failed; its recorded resources were rolled back.' -ForegroundColor Yellow }
            catch { Write-Host ('Rollback is partial; keep sharing-ownership.json and run -Restore after resolving: ' + $_.Exception.Message) -ForegroundColor Red }
        }
    }
} finally {
    if (Get-Variable -Name Lock -Scope Script -ErrorAction SilentlyContinue) { if ($null -ne $script:Lock) { $script:Lock.Dispose() } }
    if ($PauseWhenDone) { [void](Read-Host 'Press Enter to close') }
}
exit $script:ExitCode
