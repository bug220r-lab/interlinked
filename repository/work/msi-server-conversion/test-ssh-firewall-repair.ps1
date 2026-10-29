$ErrorActionPreference='Stop'
$path=Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'outputs\Project-Library-2026-10-29\Server-Setup\Repair-Coding-Server-Firewall.ps1'
$tokens=$null;$errors=$null;$ast=[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($name in @('Require','Same','AssertRuleScope')){$fn=$ast.Find({param($n)$n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true);Invoke-Expression $fn.Extent.Text}
$OwnedRule='Codex-MSI-Coding-SSH-Private-LocalSubnet-20261029'
$base=Get-Content -LiteralPath (Join-Path (Split-Path $path -Parent) 'State\SETUP-STATE.json') -Raw|ConvertFrom-Json
$rule=$base.ExpectedOwnedRule
$results=@()
AssertRuleScope $rule;$results+=[pscustomobject]@{Test='Expected current scoped rule accepted';Pass=$true}
foreach($case in @(@('Name','other'),@('Description','unowned'),@('Enabled','False'),@('Direction','Outbound'),@('Action','Block'),@('Profile','Any'),@('Protocol','UDP'),@('Program','C:\wrong.exe'),@('Service','Any'),@('Package','package'),@('PackageFamilyName','family'),@('LocalPort',@('22','23')),@('RemoteAddress',@('Any')))){
    $copy=$rule|ConvertTo-Json -Depth 10|ConvertFrom-Json;$copy.($case[0])=$case[1]
    $rejected=$false;try{AssertRuleScope $copy}catch{$rejected=$true}
    if(-not $rejected){throw ('Guard accepted drift: '+$case[0])};$results+=[pscustomobject]@{Test=('Reject '+$case[0]+' drift');Pass=$true}
}
$after=$rule|ConvertTo-Json -Depth 10|ConvertFrom-Json;$after.Service='Any'
if(Same $rule $after){throw 'Snapshot equality ignored service difference'}
$after.Service='sshd';if(-not (Same $rule $after)){throw 'Snapshot equality failed identical rule'}
$results+=[pscustomobject]@{Test='Snapshot exact equality distinguishes service condition';Pass=$true}
$fixture=Join-Path $PSScriptRoot ('replace-fixture-'+[guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($fixture)
$target=Join-Path $fixture 'state.json';$temp=Join-Path $fixture 'state.new';$backup=Join-Path $fixture 'state.old'
[IO.File]::WriteAllText($target,'before');[IO.File]::WriteAllText($temp,'after');[IO.File]::Replace($temp,$target,$backup)
if([IO.File]::ReadAllText($target) -cne 'after' -or [IO.File]::ReadAllText($backup) -cne 'before' -or (Test-Path -LiteralPath $temp)){throw 'Actual File.Replace fixture failed'}
$results+=[pscustomobject]@{Test='Actual atomic File.Replace with explicit backup';Pass=$true}
$out=Join-Path $PSScriptRoot 'ssh-firewall-repair-guard-tests.json'
[IO.File]::WriteAllText($out,(@{TestedUtc=[DateTime]::UtcNow.ToString('o');ScriptSha256=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant();Count=$results.Count;Tests=$results}|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
Write-Output ($results.Count.ToString()+' guard checks PASS')
