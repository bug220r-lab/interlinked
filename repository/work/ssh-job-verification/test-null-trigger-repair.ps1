#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$candidate=Join-Path $PSScriptRoot 'repair-candidate\Invoke-SSH-Job-Verification.ps1'
$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($candidate,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count){throw ($errors|Out-String)}
$lines=[IO.File]::ReadAllLines($candidate)
$first=@($lines|Where-Object {$_ -like '*Scheduled task principal/action/trigger ownership differs.*'})
if($first.Count -ne 1 -or $first[0] -notmatch "^\s*Require \((.*)\) 'Scheduled task principal/action/trigger ownership differs\.'$"){throw 'Expected exact ownership predicate'}
$predicate=[scriptblock]::Create('param($task,$principalSid,$plan) '+$Matches[1])
$second=@($lines|Where-Object {$_ -like '*Scheduled task action differs from the fixed helper.*'})
if($second.Count -ne 1 -or $second[0] -notmatch "^\s*Require \((.*)\) 'Scheduled task action differs from the fixed helper\.'$"){throw 'Expected exact action predicate'}
$actionPredicate=[scriptblock]::Create('param($task,$powerShell,$expectedArgs,$jobFolder) '+$Matches[1])
$plan=[pscustomobject]@{ExpectedSid='S-1-5-21-1-2-3-1001'};$powerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';$expectedArgs='fixed reviewed args';$jobFolder='C:\owned-job'
function NewTask {[pscustomobject]@{Principal=[pscustomobject]@{LogonType='Interactive';RunLevel='Limited'};Actions=@([pscustomobject]@{Execute=$powerShell;Arguments=$expectedArgs;WorkingDirectory=$jobFolder});Triggers=$null}}
$results=@()
function Check([string]$name,$task,[string]$sid,[bool]$expected){$actual=(& $predicate $task $sid $plan) -and (& $actionPredicate $task $powerShell $expectedArgs $jobFolder);if($actual -ne $expected){throw ('Unexpected result: '+$name)};$script:results+=[pscustomobject]@{Test=$name;Pass=$true}}
$t=NewTask
if(@($t.Triggers).Count -ne 1){throw 'Regression fixture did not reproduce original null count bug'}
Check 'Null Triggers accepted; original expression incorrectly counted one' $t $plan.ExpectedSid $true
$t=NewTask;$t.Triggers=@();Check 'Empty trigger list accepted' $t $plan.ExpectedSid $true
$t=NewTask;$t.Triggers=@([pscustomobject]@{Enabled=$true});Check 'Real trigger rejected' $t $plan.ExpectedSid $false
$t=NewTask;$t.Actions=@();Check 'Missing action rejected' $t $plan.ExpectedSid $false
$t=NewTask;Check 'Different principal SID rejected' $t 'S-1-5-21-1-2-3-9999' $false
$t=NewTask;$t.Principal.LogonType='Password';Check 'Noninteractive principal rejected' $t $plan.ExpectedSid $false
$t=NewTask;$t.Principal.RunLevel='Highest';Check 'Elevated principal rejected' $t $plan.ExpectedSid $false
$t=NewTask;$t.Actions[0].Execute='C:\other.exe';Check 'Different action executable rejected' $t $plan.ExpectedSid $false
$t=NewTask;$t.Actions[0].Arguments='changed args';Check 'Different action arguments rejected' $t $plan.ExpectedSid $false
$path=Join-Path $PSScriptRoot 'null-trigger-repair-tests.json'
[IO.File]::WriteAllText($path,(@{TestedUtc=[DateTime]::UtcNow.ToString('o');PowerShellVersion=$PSVersionTable.PSVersion.ToString();CandidateSha256=(Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant();Checks=$results}|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
Write-Output ($results.Count.ToString()+' regression/boundary checks PASS')
