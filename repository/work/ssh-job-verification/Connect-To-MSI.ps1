#requires -Version 5.1
<# Local THIS-MACHINE entry point. Native ssh owns the interactive password
prompt; stdin/stdout/stderr are inherited, never redirected or transcribed.
Uses only a private per-run known_hosts file and the reviewed server public pin. #>
[CmdletBinding()]
param([ValidateSet('Verify','Terminal')][string]$Mode='Verify')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$kit=[IO.Path]::GetFullPath($PSScriptRoot)
$preflightReceiptAllowed=$false
try{
if($kit -notmatch '^[A-Za-z]:\\' -or [Environment]::MachineName -ine 'THIS-MACHINE'){throw 'Run locally on the new PC named THIS-MACHINE.'}
$walk=Get-Item -LiteralPath $kit -Force
while($null -ne $walk){if($walk -isnot [IO.DirectoryInfo] -or ($walk.Attributes -band (0x400 -bor 0x1000 -bor 0x40000 -bor 0x400000))){throw 'Local kit has a linked/cloud ancestor.'};$walk=$walk.Parent}
$preflightReceiptAllowed=$true
$pre=Get-Content -LiteralPath (Join-Path $kit 'SSH-Verification-Plan.json') -Raw|ConvertFrom-Json
if((Get-FileHash -LiteralPath (Join-Path $kit 'SSH-Verification-Common.ps1') -Algorithm SHA256).Hash -ine $pre.CommonSha256){throw 'Shared helper hash differs.'}
. (Join-Path $kit 'SSH-Verification-Common.ps1')
[void](Plain $kit $true)
$plan=Load-PinnedPlan $kit
Require ((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash -ieq $plan.ClientSha256) 'Connection helper hash differs.'
$package='C:\Users\jlewi\Documents\Codex\2026-10-28\prep\outputs\Project-Library-2026-10-29'
$fixtureFolder='SSH-Job-Verification-'+$plan.FixtureId
$control='\\MSI\ProjectLibrary\'+$fixtureFolder
$returns='\\MSI\ProjectReturns\'+$fixtureFolder
$controller=Join-Path (Join-Path (Join-Path $package 'HostLibrary') $fixtureFolder) 'Invoke-SSH-Job-Verification.ps1'
Require ($plan.ControllerPath -ceq $controller -and $plan.ControlUNC -ceq $control -and $plan.ReturnsUNC -ceq $returns) 'Unexpected pinned server fixture paths.'
$ssh='C:\Windows\System32\OpenSSH\ssh.exe';[void](Plain $ssh)
Require (Test-Path -LiteralPath $ssh -PathType Leaf) 'Windows SSH client is missing; this helper will not install it.'
}catch{
    $message=$_.Exception.Message
    if($preflightReceiptAllowed){
        try{
            $failurePath=Join-Path $kit ('SSH-Preflight-Failure-'+[guid]::NewGuid().ToString('N')+'.json')
            $failure=[ordered]@{Format='new-pc-msi-ssh-verification-v1';Status='BLOCKED';Phase='PREFLIGHT';Utc=[DateTime]::UtcNow.ToString('o');Failure=$message;SshLaunched=$false;PasswordCaptured=$false}
            $bytes=[Text.Encoding]::UTF8.GetBytes(($failure|ConvertTo-Json));$stream=[IO.FileStream]::new($failurePath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
            Write-Host ('Preflight receipt: '+$failurePath)
        }catch{Write-Warning 'A local preflight receipt could not be saved.'}
    }
    Write-Host ('Blocked before SSH: '+$message);exit 2
}
$runId=[guid]::NewGuid().ToString('N');$run=Join-Path $kit ('SSH-Verification-Run-'+$runId)
Require (-not (Test-Path -LiteralPath $run)) 'Fresh verification directory already exists.'
[void][IO.Directory]::CreateDirectory($run)
$result=[ordered]@{Format='new-pc-msi-ssh-verification-v1';Mode=$Mode;Status='PREFLIGHT';ClientRunId=$runId;FixtureId=$plan.FixtureId;StartedUtc=[DateTime]::UtcNow.ToString('o');Computer=[Environment]::MachineName;SshEndpoint='jlewi@MSI (192.168.247.1)';PasswordCaptured=$false;SshStreamsCaptured=$false;GlobalSshConfigChanged=$false;GlobalKnownHostsChanged=$false;ProductionDataUsed=$false;SshDisconnectSurvivalTested=$false;Failure=$null}
$exitCode=2
try{
    Require ((Hash-Bytes (Read-Small (Join-Path $control 'SSH-Verification-Plan.json'))) -ceq (Hash-Bytes (Read-Small (Join-Path $kit 'SSH-Verification-Plan.json')))) 'Published server plan differs from local pinned plan.'
    Require ((Get-FileHash -LiteralPath (Join-Path $control 'Invoke-SSH-Job-Verification.ps1') -Algorithm SHA256).Hash -ieq $plan.ControllerSha256) 'Published controller differs from its pin.'
    Require ($plan.HostPublicKey.TrustSource -ceq 'MSI_LOCAL_PUBLIC_KEY_READ_AND_FINGERPRINT_VERIFIED' -and $plan.HostPublicKey.Algorithm -ceq 'ssh-ed25519') 'A trusted MSI public host-key pin is required; ssh-keyscan alone is not accepted.'
    $keyText=[string]$plan.HostPublicKey.Base64
    Require ($keyText -cmatch '^[A-Za-z0-9+/]+={0,2}$') 'Invalid public key encoding.'
    $key=[Convert]::FromBase64String($keyText)
    Require ($key.Length -eq 51 -and [Convert]::ToBase64String($key) -ceq $keyText) 'Unexpected Ed25519 key bytes.'
    Require (($key[0..3] -join ',') -ceq '0,0,0,11' -and [Text.Encoding]::ASCII.GetString($key,4,11) -ceq 'ssh-ed25519' -and ($key[15..18] -join ',') -ceq '0,0,0,32') 'Invalid SSH public key wire format.'
    $hasher=[Security.Cryptography.SHA256]::Create();try{$fingerprint='SHA256:'+([Convert]::ToBase64String($hasher.ComputeHash($key))).TrimEnd('=')}finally{$hasher.Dispose()}
    Require ($fingerprint -ceq $plan.HostPublicKey.Fingerprint) 'Host public-key fingerprint does not match the trusted pin.'
    $known=Join-Path $run 'known_hosts'
    New-Bytes $known ([Text.Encoding]::ASCII.GetBytes('MSI,192.168.247.1 ssh-ed25519 '+$keyText+"`n"))
    $result.HostPublicKeyFingerprint=$fingerprint;$result.KnownHosts=$known
    $arguments=@('-F','NUL','-o','HostName=192.168.247.1','-o','HostKeyAlias=MSI',
        '-o','UserKnownHostsFile=known_hosts','-o','GlobalKnownHostsFile=NUL',
        '-o','StrictHostKeyChecking=yes','-o','VerifyHostKeyDNS=no','-o','UpdateHostKeys=no',
        '-o','CheckHostIP=yes','-o','HostKeyAlgorithms=ssh-ed25519',
        '-o','PreferredAuthentications=password','-o','PasswordAuthentication=yes',
        '-o','PubkeyAuthentication=no','-o','KbdInteractiveAuthentication=no',
        '-o','NumberOfPasswordPrompts=1','-o','IdentityAgent=none','-o','ForwardAgent=no',
        '-o','ClearAllForwardings=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=10',
        '-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3','-o','LogLevel=ERROR')
    $remotePowerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    if($Mode -eq 'Verify'){
        $arguments+='-T'
        # Every dynamic token is an exact pinned absolute path or 32 hex digits.
        $remoteCommand=$remotePowerShell+' -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+$controller+' -FixtureId '+$plan.FixtureId+' -ClientRunId '+$runId
    }else{
        $arguments+='-tt'
        # Reviewed static bootstrap only. -NoExit leaves the user's interactive
        # PowerShell session in Server-Setup after identity verification.
        $bootstrap=@'
$ErrorActionPreference='Stop'
$i=[Security.Principal.WindowsIdentity]::GetCurrent()
if([Environment]::MachineName -ine 'MSI' -or $i.User.Value -ne 'S-1-5-21-3086848077-1833151167-136462239-1001'){Write-Error 'Unexpected remote identity';exit 23}
Set-Location -LiteralPath 'C:\Users\jlewi\Documents\Codex\2026-10-28\prep\outputs\Project-Library-2026-10-29\Server-Setup'
Write-Host ('Verified MSI session as '+$i.Name+'. Current folder: Server-Setup. Type exit when finished.')
'@
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
        $result.StaticTerminalBootstrapSha256=Hash-Bytes ([Text.Encoding]::UTF8.GetBytes($bootstrap))
        $remoteCommand=$remotePowerShell+' -NoLogo -NoProfile -NoExit -EncodedCommand '+$encoded
    }
    $arguments+=@('jlewi@MSI',$remoteCommand)
    Write-Host ('Pinned MSI host key: '+$fingerprint)
    Write-Host 'Enter the MSI Windows password only at the native ssh prompt. Input is not displayed or recorded by this helper.'
    if($Mode -eq 'Verify'){Write-Host 'This starts only the reviewed synthetic job (about 12 seconds), then checks its completion.'}
    $info=New-Object Diagnostics.ProcessStartInfo;$info.FileName=$ssh;$info.Arguments=(($arguments|ForEach-Object{Quote-Arg $_}) -join ' ')
    $info.UseShellExecute=$false;$info.CreateNoWindow=$false;$info.WorkingDirectory=$run
    # Deliberately no RedirectStandardInput/Output/Error, transcript or ssh log.
    $process=New-Object Diagnostics.Process;$process.StartInfo=$info
    try{Require ($process.Start()) 'Could not launch native Windows SSH.';$process.WaitForExit();$result.SshExitCode=$process.ExitCode}finally{$process.Dispose()}
    if($Mode -eq 'Terminal'){
        Require ($result.SshExitCode -eq 0) 'Interactive SSH session did not exit successfully.'
        $result.Status='INTERACTIVE_SESSION_EXITED';$exitCode=0
    }else{
        Require ($result.SshExitCode -eq 0) 'SSH authentication/controller did not complete successfully. No PASS is claimed; inspect retained MSI receipts before retrying.'
        $remoteRun=Join-Path $returns ('Run-'+$runId)
        $receiptBytes=Read-Small (Join-Path $remoteRun 'result.json')
        $receipt=[Text.Encoding]::UTF8.GetString($receiptBytes)|ConvertFrom-Json
        Require ($receipt.Format -ceq 'msi-ssh-job-acceptance-v1' -and $receipt.Status -ceq 'PASS' -and $receipt.FixtureId -ceq $plan.FixtureId -and $receipt.ClientRunId -ceq $runId -and $receipt.ProjectId -ceq $plan.ProjectId) 'Remote acceptance receipt identity/status differs.'
        Require ($receipt.Computer -ieq 'MSI' -and $receipt.Sid -ceq $plan.ExpectedSid -and $receipt.IdentityVerified -eq $true -and $receipt.RunnerStatus -ceq 'COMPLETED' -and $receipt.RunnerExitCode -eq 0 -and $receipt.LastTaskResult -eq 0 -and $receipt.TaskState -ceq 'Ready' -and $receipt.LaunchChildExitedBeforeJobCompletion -eq $true) 'Remote identity/job completion evidence is incomplete.'
        Require ($receipt.ManualSyntheticRunnerBaseline -eq $true -and $receipt.ProductionFetchPerformed -eq $false -and $receipt.ProductionDataUsed -eq $false) 'Unexpected test scope.'
        Require ($receipt.OutputRelative -ceq 'job-result.json') 'Unexpected output receipt path.'
        $outputBytes=Read-Small (Join-Path $remoteRun 'job-result.json')
        Require ($outputBytes.Length -eq $receipt.OutputBytes -and (Hash-Bytes $outputBytes) -ceq $receipt.OutputSha256) 'Returned output byte/hash verification failed.'
        $output=[Text.Encoding]::UTF8.GetString($outputBytes)|ConvertFrom-Json
        Require ($output.fixtureId -ceq $plan.FixtureId -and $output.projectId -ceq $plan.ProjectId -and $output.computer -ieq 'MSI' -and $output.inputSha256 -ceq $plan.InputSha256 -and $output.sumOfSquares1Through10000 -eq 333383335000 -and $output.syntheticOnly -eq $true) 'Returned synthetic result differs.'
        New-Bytes (Join-Path $run 'server-result.json') $receiptBytes
        New-Bytes (Join-Path $run 'job-result.json') $outputBytes
        $result.Status='PASS_ACTUAL_NEW_PC_SSH_JOB';$result.JobId=$receipt.JobId;$result.OutputSha256=$receipt.OutputSha256;$result.RemoteReceipt=Join-Path $remoteRun 'result.json';$result.ServerReceiptSha256=Hash-Bytes $receiptBytes;$result.ManualSyntheticRunnerBaseline=$true;$exitCode=0
    }
}catch{$result.Status='INCOMPLETE';$result.Failure=$_.Exception.Message;Write-Host ('Stopped: '+$result.Failure)}
$result.CompletedUtc=[DateTime]::UtcNow.ToString('o')
New-Json (Join-Path $run 'result.json') $result
Write-Host ('Result: '+$result.Status)
Write-Host ('Local receipt: '+(Join-Path $run 'result.json'))
exit $exitCode
