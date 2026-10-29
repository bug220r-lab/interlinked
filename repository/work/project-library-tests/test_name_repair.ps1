$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$path=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\outputs\Project-Library-2026-10-29\Repair-Direct-PC-Names.ps1'))
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$parseErrors)
if(@($parseErrors).Count){throw ($parseErrors | Out-String)}
foreach($fn in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
    if($fn.Name -eq 'Test-PeerTcp'){continue}
    . ([scriptblock]::Create($fn.Extent.Text))
}
$cases=@()
$ascii=[Text.Encoding]::ASCII.GetBytes("# original comment`r`n127.0.0.1 localhost`r`n")
$plan=Plan-HostsAppend $ascii 'MSI' '192.168.247.1'
Require (-not $plan.AlreadyCorrect -and $plan.AppendBytes.Length -gt 0) 'Missing mapping did not append.'
$combined=[byte[]]($ascii+$plan.AppendBytes)
Require (([Text.Encoding]::ASCII.GetString($combined)).StartsWith([Text.Encoding]::ASCII.GetString($ascii))) 'Existing bytes changed.'
$cases+='ASCII append preserves original bytes'
$again=Plan-HostsAppend $combined 'MSI' '192.168.247.1'
Require ($again.AlreadyCorrect -and $again.AppendBytes.Length -eq 0) 'Repeat repair not idempotent.'
$cases+='Already-correct mapping is idempotent'
foreach($bad in @("127.0.0.1 MSI`n","192.168.247.1 MSI`n192.168.247.1 msi`n","fe80::1 MSI`n")){
    $blocked=$false
    try{Plan-HostsAppend ([Text.Encoding]::ASCII.GetBytes($bad)) 'MSI' '192.168.247.1' | Out-Null}catch{$blocked=$true}
    Require $blocked 'Conflicting or duplicate peer mapping was accepted.'
}
$cases+='Conflicting duplicate and IPv6 mappings refused'
$bom=[byte[]](239,187,191)+[Text.Encoding]::UTF8.GetBytes("# caf$([char]233)`n")
$utf=Plan-HostsAppend $bom 'THIS-MACHINE' '192.168.247.2'
Require ($utf.Encoding -eq 'UTF8-BOM') 'Explicit UTF8 BOM not preserved.'
$cases+='Explicit UTF8 BOM accepted'
foreach($badBytes in @(([byte[]](255,254,65,0)),([byte[]](35,32,233)),([byte[]](65,0,66)))){
    $blocked=$false
    try{Plan-HostsAppend $badBytes 'MSI' '192.168.247.1' | Out-Null}catch{$blocked=$true}
    Require $blocked 'Ambiguous encoding was accepted.'
}
$cases+='UTF16 nonASCII-without-BOM and NUL refused'
$lf=Plan-HostsAppend ([Text.Encoding]::ASCII.GetBytes("# x`n")) 'MSI' '192.168.247.1'
Require (-not ([Text.Encoding]::ASCII.GetString($lf.AppendBytes)).Contains("`r")) 'LF convention was not retained.'
$cases+='Existing LF line endings retained'
$dot=Plan-HostsAppend ([Text.Encoding]::ASCII.GetBytes("192.168.247.1 msI. # local peer`n")) 'MSI' '192.168.247.1'
Require $dot.AlreadyCorrect 'Case-insensitive trailing-dot alias not recognized.'
$cases+='Case and trailing-dot alias recognized'
$folder=Join-Path $PSScriptRoot ('hosts-name-fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
$backup=Join-Path $folder 'original.bin'
Save-NewBytes $backup $ascii
$before=(Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
$blocked=$false
try{Save-NewBytes $backup ([Text.Encoding]::ASCII.GetBytes('different'))}catch{$blocked=$true}
Require ($blocked -and (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -eq $before) 'Preexisting backup was overwritten.'
$cases+='CreateNew backup cannot overwrite'
$stream=New-Object IO.FileStream($backup,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{Require ((Hash-Bytes (Read-ExactBytes $stream)) -ceq (Hash-Bytes $ascii)) 'Exact byte reader failed.'}finally{$stream.Dispose()}
$cases+='Exact byte reader hash matches'
$report=[ordered]@{Status='PASS';PowerShellVersion=$PSVersionTable.PSVersion.ToString();ScriptSha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash;Checks=$cases;NetworkAccessed=$false;ActualHostsReadOrChanged=$false;ProductionScriptExecuted=$false;Fixture=$folder}
$json=$report | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'name-repair-validation.json'),$json,[Text.UTF8Encoding]::new($false))
$json
