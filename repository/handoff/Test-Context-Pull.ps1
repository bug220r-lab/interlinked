[CmdletBinding()]param()
$ErrorActionPreference='Stop'
$base=Split-Path -Parent $PSCommandPath
$script=Join-Path (Split-Path -Parent (Split-Path -Parent $base)) 'repository\handoff\Pull-INTERLINKED-Context.ps1'
$errors=$null;$tokens=$null
[void][Management.Automation.Language.Parser]::ParseFile($script,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
. $script -DefineOnly
Initialize-ContextReader
$checks=New-Object 'System.Collections.Generic.List[string]'
if((Get-ContextCheckout 'C:\Users\Jude\Documents\Codex\INTERLINKED\repository\handoff') -cne 'C:\Users\Jude\Documents\Codex\INTERLINKED'){throw 'Outer clone root derivation differs'};$checks.Add('Tracked repository/handoff layout resolves outer clone root for ignored .handoff')
$failed=$false;try{[void](Get-ContextCheckout 'C:\Users\Jude\Desktop\handoff')}catch{$failed=$true};if(!$failed){throw 'Relocated helper accepted'};$checks.Add('Relocated helper layout rejected')
$fixture=Join-Path $base ('fixture-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$sourceDir=Join-Path $fixture 'source';$dest=Join-Path $fixture 'destination'
[void][IO.Directory]::CreateDirectory($sourceDir);[void][IO.Directory]::CreateDirectory($dest)
$source=Join-Path $sourceDir 'note.json';$text='{"synthetic":true,"note":"'+[char]0x03a9+'"}'
[IO.File]::WriteAllText($source,$text,(New-Object Text.UTF8Encoding($false)))
$sha=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$row=[pscustomobject]@{relativePath='notes/note.json';bytes=(Get-Item -LiteralPath $source).Length;sha256=$sha}
$name='INTERLINKED-Codex-Handoff-2026-10-29-'+('a'*32)
$manifest=[pscustomobject]@{format='interlinked-context-capsule-v1';capsuleName=$name;files=@($row)}
if((Assert-ContextManifest $manifest $name) -ne $row.bytes){throw 'Manifest count failed'};$checks.Add('Exact bounded capsule manifest accepted')
Ensure-ContextParents $dest 'notes\note.json'
$target=Join-Path $dest 'notes\note.json';$before=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$result=Copy-ContextEntry $source $target $row
if($result.status -ne 'COPIED_AND_HASH_VERIFIED' -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha){throw 'Native copy mismatch'};$checks.Add('Native readlock CreateNew fsync and destination SHA readback pass')
$failed=$false;try{[void](Copy-ContextEntry $source $target $row)}catch{$failed=$true};if(!$failed){throw 'Existing destination overwritten'};$checks.Add('Existing destination refuses overwrite')
if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha){throw 'Existing file mutated'};$checks.Add('Refused overwrite retains original bytes')
$row.sha256=('0'*64);$bad=Join-Path $dest 'bad.json';$failed=$false
try{[void](Copy-ContextEntry $source $bad $row)}catch{$failed=$true};if(!$failed -or (Test-Path -LiteralPath $bad)){throw 'Bad source hash copied'};$checks.Add('Source mismatch refuses before destination file creation');$row.sha256=$sha
foreach($badPath in @('../escape.json','C:/outside.json','/absolute.json','a//b.json','a/./b.json','a\b.json','a/name:stream.json','a/CON.json','a/LPT2.json','a/trailing./b.json','manifest.json','_pull-receipt.json','payload.MVP','script.ps1')){
    $failed=$false;try{[void](Assert-ContextRelative $badPath)}catch{$failed=$true};if(!$failed){throw ('Accepted '+$badPath)};$checks.Add('Relative/extension guard rejects '+$badPath)
}
$manifest.files=@($row,$row);$failed=$false;try{[void](Assert-ContextManifest $manifest $name)}catch{$failed=$true};if(!$failed){throw 'Duplicate paths accepted'};$checks.Add('Duplicate case-insensitive destinations rejected');$manifest.files=@($row)
$collision=[pscustomobject]@{relativePath='notes/note.json/child.txt';bytes=0;sha256=('0'*64)};$manifest.files=@($row,$collision);$failed=$false;try{[void](Assert-ContextManifest $manifest $name)}catch{$failed=$true};if(!$failed){throw 'Prefix collision accepted'};$checks.Add('File/directory prefix collision rejected before copy');$manifest.files=@($row)
$row.bytes=33554433;$failed=$false;try{[void](Assert-ContextManifest $manifest $name)}catch{$failed=$true};if(!$failed){throw 'Oversized metadata accepted'};$checks.Add('Per-file32MiB bound enforced');$row.bytes=(Get-Item -LiteralPath $source).Length
$outside=Join-Path $fixture 'outside';[void][IO.Directory]::CreateDirectory($outside)
$junction=Join-Path $dest 'foreign';[void](New-Item -ItemType Junction -Path $junction -Target $outside)
$failed=$false;try{Ensure-ContextParents $dest 'foreign\must-not-create\note.json'}catch{$failed=$true}
if(!$failed -or (Test-Path -LiteralPath (Join-Path $outside 'must-not-create'))){throw 'Directory creation followed junction'};$checks.Add('Parent walk refuses junction before creating outside descendants')
[IO.File]::WriteAllText((Join-Path $outside 'source.json'),'fixture only')
$failed=$false;try{[void][InterlinkedContextReader]::Read((Join-Path $junction 'source.json'),100)}catch{$failed=$true};if(!$failed){throw 'Source junction followed'};$checks.Add('Source reparse ancestor rejected')
$write=New-Object IO.FileStream($source,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{$failed=$false;try{[void][InterlinkedContextReader]::Read($source,1024)}catch{$failed=$true};if(!$failed){throw 'Writer lock accepted'}}finally{$write.Dispose()};$checks.Add('Concurrent writer prevents snapshot read')
if((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $before){throw 'Source mutated'};$checks.Add('Synthetic source remains byte-identical')
$report=[ordered]@{status='PASS';count=$checks.Count;checks=$checks.ToArray();scriptSha256=(Get-FileHash -LiteralPath $script -Algorithm SHA256).Hash.ToLowerInvariant();fixtureRoot=$fixture;noNetwork=$true;noProductionData=$true}
[IO.File]::WriteAllText((Join-Path $fixture 'results.json'),($report | ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
$report | ConvertTo-Json -Depth 5
