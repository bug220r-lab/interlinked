# Local fixtures/config parsing only. No SSH connection, login, jobs or settings.
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$scripts=@('SSH-Verification-Common.ps1','Connect-To-MSI.ps1','Invoke-SSH-Job-Verification.ps1')
$checks=@();$asts=@{}
foreach($name in $scripts){$tokens=$null;$errors=$null;$asts[$name]=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$tokens,[ref]$errors);if(@($errors).Count){throw ($errors|Out-String)}}
$checks+='All three scripts parse in Windows PowerShell 5.1'
. (Join-Path $PSScriptRoot 'SSH-Verification-Common.ps1')
$folder=Join-Path $PSScriptRoot ('Test Fixtures With Spaces '+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
$data=[Text.Encoding]::UTF8.GetBytes('synthetic pinned bytes')
$file=Join-Path $folder 'fixture.txt';New-Bytes $file $data
$handle=Pin-File $file (Hash-Bytes $data) $data.Length
try{$blocked=$false;try{$writer=[IO.FileStream]::new($file,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$writer.Dispose()}catch{$blocked=$true};Require $blocked 'Pinned file did not block a writer.'}finally{$handle.Dispose()}
$checks+='Pinned hash/length file handle blocks concurrent writes'
$blocked=$false;try{Pin-File $file ('0'*64)|Out-Null}catch{$blocked=$true};Require $blocked 'Changed hash was accepted.'
$checks+='Incorrect source hash fails closed'
$blocked=$false;try{New-Bytes $file ([byte[]](1,2,3))}catch{$blocked=$true};Require ($blocked -and (Hash-Bytes (Read-Small $file)) -ceq (Hash-Bytes $data)) 'CreateNew overwrote prior content.'
$checks+='CreateNew preserves existing evidence'
$template=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Prepared\SSH-Verification-Plan.json') -Raw|ConvertFrom-Json
$planPath=Join-Path $folder 'SSH-Verification-Plan.json';$pinPath=Join-Path $folder 'SSH-Verification-Plan.sha256'
$planBytes=[Text.Encoding]::UTF8.GetBytes(($template|ConvertTo-Json -Depth 12));New-Bytes $planPath $planBytes;New-Bytes $pinPath ([Text.Encoding]::ASCII.GetBytes((Hash-Bytes $planBytes)))
$blocked=$false;try{Load-PinnedPlan $folder|Out-Null}catch{$blocked=$true};Require $blocked 'Unready template was accepted.'
$checks+='Unready server/public-key plan blocks before connection'
$template.Status='READY_FOR_ACTUAL_NEW_PC_TEST';$planBytes=[Text.Encoding]::UTF8.GetBytes(($template|ConvertTo-Json -Depth 12));[IO.File]::WriteAllBytes($planPath,$planBytes);[IO.File]::WriteAllText($pinPath,(Hash-Bytes $planBytes))
$loaded=Load-PinnedPlan $folder;Require ($loaded.FixtureId -eq $template.FixtureId) 'Pinned metadata parser differs.'
[IO.File]::AppendAllText($planPath,' ');$blocked=$false;try{Load-PinnedPlan $folder|Out-Null}catch{$blocked=$true};Require $blocked 'Plan hash mismatch was accepted.'
$checks+='Ready metadata parses and any changed plan bytes are refused'
# Use the actual client argument array with ssh -G. This prints configuration
# without contacting a host; native streams here contain no authentication.
$clientAst=$asts['Connect-To-MSI.ps1']
$assignment=@($clientAst.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$arguments' -and $n.Right.Extent.Text -like '@(*'},$true))[0]
. ([scriptblock]::Create('$testArgs='+$assignment.Right.Extent.Text))
$testArgs=@('-G')+$testArgs+@('jlewi@MSI')
$info=New-Object Diagnostics.ProcessStartInfo;$info.FileName='C:\Windows\System32\OpenSSH\ssh.exe';$info.Arguments=(($testArgs|ForEach-Object{Quote-Arg $_}) -join ' ');$info.WorkingDirectory=$folder;$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
$process=New-Object Diagnostics.Process;$process.StartInfo=$info
try{Require ($process.Start()) 'ssh -G did not start.';$out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync();Require ($process.WaitForExit(10000)) 'ssh -G timed out.';$configuration=$out.GetAwaiter().GetResult();$diagnostic=$err.GetAwaiter().GetResult();Require ($process.ExitCode -eq 0) ('ssh -G parse failed: '+$diagnostic)}finally{if(-not $process.HasExited){$process.Kill()};$process.Dispose()}
$values=@{}
foreach($line in ($configuration -split "`n")){if($line -match '^([^ ]+) (.*)$'){$values[$matches[1]]=$matches[2].Trim()}}
Require ($values['hostname'] -ceq '192.168.247.1' -and $values['user'] -ceq 'jlewi') 'Endpoint differs.'
Require ($values['userknownhostsfile'] -ceq 'known_hosts' -and $values['globalknownhostsfile'] -ceq 'NUL') 'Known-hosts configuration differs.'
Require ($values['stricthostkeychecking'] -in @('true','yes') -and $values['numberofpasswordprompts'] -eq '1') 'Host-key/password-attempt guard differs.'
Require ($values['preferredauthentications'] -ceq 'password' -and $values['pubkeyauthentication'] -in @('false','no') -and $values['forwardagent'] -in @('false','no') -and $values['identityagent'] -eq 'none') 'Unexpected credential/agent behavior.'
$checks+='Actual ssh -G accepts pinned flags with a spaced run directory and relative known_hosts'
$hashes=@{};foreach($name in $scripts){$hashes[$name]=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()}
$result=[ordered]@{Status='PASS';PowerShellVersion=$PSVersionTable.PSVersion.ToString();Checks=$checks;Hashes=$hashes;SshConfigurationOnly=$true;NetworkConnectionAttempted=$false;AuthenticationAttempted=$false;TaskStarted=$false;ProductionDataUsed=$false;Fixture=$folder}
$json=$result|ConvertTo-Json -Depth 6;[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'preparation-test-results.json'),$json,[Text.UTF8Encoding]::new($false));$json
