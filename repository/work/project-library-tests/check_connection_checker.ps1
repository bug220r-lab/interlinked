$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$scriptPath=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\outputs\Project-Library-2026-10-29\Check-MSI-Connection.ps1'))
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$parseErrors)
if(@($parseErrors).Count){throw ($parseErrors | Out-String)}
$fixture=Join-Path $PSScriptRoot ('connection-check-fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$functions=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)
foreach($fn in $functions) {
    if($fn.Name -eq 'Test-BoundedTcp'){continue} # No network probes during validation.
    $definition=$fn.Extent.Text
    if($fn.Name -eq 'Test-ReturnsScratch') {
        # Only the fixture copy substitutes an owned local directory for fixed UNC.
        $definition=$definition.Replace("'\\MSI\ProjectReturns'",("'"+$fixture.Replace("'","''")+"'"))
    }
    . ([scriptblock]::Create($definition))
}
$catalog=Join-Path $fixture 'catalog.json'
[IO.File]::WriteAllText($catalog,'{"format":"project-library-catalog-v1","catalogId":"fixture-v1","projects":[]}')
$header=Test-CatalogHeader $fixture
if($header.Status -ne 'HEADER_READ' -or $header.CatalogId -ne 'fixture-v1'){throw 'Catalog fixture failed.'}
$id=[Guid]::NewGuid().ToString('N')
$scratch=Test-ReturnsScratch $fixture $id
if(-not $scratch.ReadbackVerified -or -not $scratch.TemporaryFileRemoved){throw 'Scratch fixture failed.'}
$conflict=Join-Path $fixture ('Connection-Check-THIS-MACHINE-'+$id+'.tmp')
[IO.File]::WriteAllText($conflict,'existing fixture bytes must be retained')
$before=(Get-FileHash -LiteralPath $conflict -Algorithm SHA256).Hash
$blocked=$false
try{Test-ReturnsScratch $fixture $id | Out-Null}catch{$blocked=$true}
if(-not $blocked -or (Get-FileHash -LiteralPath $conflict -Algorithm SHA256).Hash -ne $before){throw 'CreateNew collision did not preserve preexisting fixture.'}
$traversalBlocked=$false
try{Test-ReturnsScratch $fixture '../bad' | Out-Null}catch{$traversalBlocked=$true}
if(-not $traversalBlocked){throw 'Invalid scratch ID was accepted.'}
$kit=Assert-LocalKitDirectory $PSScriptRoot
if($kit -ne $PSScriptRoot){throw 'Local kit path validation mismatch.'}
$report=[ordered]@{Status='PASS';PowerShellVersion=$PSVersionTable.PSVersion.ToString();ScriptSha256=(Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash;Checks=@('PS5 parse','catalog header fixture','scratch write/read SHA256/delete-on-close','CreateNew conflict preservation','invalid scratch ID blocked','normal local kit accepted');NetworkAccessed=$false;ProductionScriptExecuted=$false;Fixture=$fixture}
$json=$report | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'connection-checker-validation.json'),$json,[Text.UTF8Encoding]::new($false))
$json
