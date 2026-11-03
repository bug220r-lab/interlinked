# Packs the reviewed private source artwork into a bounded worker-only resource.
# No application, device, sensor, model or installed-state operation is performed.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PackageRoot,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$package=(Resolve-Path -LiteralPath $PackageRoot).Path
$output=[IO.Path]::GetFullPath($OutputPath)
if(Test-Path -LiteralPath $output){throw 'Use an absent animation resource output.'}
function Ordinary([string]$path){
 $walk=[IO.Path]::GetFullPath($path)
 while($walk){if(([long](Get-Item -LiteralPath $walk -Force).Attributes -band 0x441400)-ne0){throw 'Animation inputs must be ordinary local files.'};$walk=[IO.Path]::GetDirectoryName($walk)}
}
Ordinary $package
Ordinary ([IO.Path]::GetDirectoryName($output))
$manifestPath=Join-Path $package 'animation-manifest.json'
$sealPath=Join-Path $package 'CHECKSUMS.json'
Ordinary $manifestPath;Ordinary $sealPath
function HashBytes([byte[]]$bytes){$hasher=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}}
$manifestBytes=[IO.File]::ReadAllBytes($manifestPath);$sealBytes=[IO.File]::ReadAllBytes($sealPath)
if((HashBytes $manifestBytes) -cne 'e7267f338b6ce53794f6c420451bb9be733f48e538cd429cc465c43205661c02'){throw 'Reviewed animation manifest differs.'}
if((HashBytes $sealBytes) -cne '0f6c95e5c78d9389678ae0ab2769638c3fe258f1a33a3b7c703861ef960450af'){throw 'Reviewed animation source seal differs.'}
$manifest=[Text.UTF8Encoding]::new($false,$true).GetString($manifestBytes)|ConvertFrom-Json
$seal=[Text.UTF8Encoding]::new($false,$true).GetString($sealBytes)|ConvertFrom-Json
$spec=[ordered]@{
 new_thinking=@(48,8,'loop');new_reviewing=@(54,9,'loop');new_success=@(48,8,'once');new_finished=@(54,9,'once');
 new_listening=@(42,7,'loop');new_speaking=@(30,5,'loop');new_reconnecting=@(60,10,'loop');new_resting=@(78,13,'loop');
 new_greeting=@(48,8,'once');new_concerned=@(60,10,'loop');legacy_neutral=@(36,6,'loop');legacy_failure=@(48,6,'loop');legacy_waiting=@(36,6,'loop');legacy_working=@(36,6,'loop')
}
if($manifest.fps.num-ne30 -or $manifest.fps.den-ne1 -or @($manifest.animations).Count-ne14){throw 'Reviewed cadence or membership differs.'}
$descriptors=New-Object 'Collections.Generic.List[object]'
$blobs=New-Object 'Collections.Generic.List[byte[]]'
$total=0
foreach($id in $spec.Keys){
 $rows=@($manifest.animations|Where-Object {$_.id -ceq $id})
 if($rows.Count-ne1){throw 'Exactly one reviewed animation descriptor is required.'}
 $row=$rows[0];$want=$spec[$id]
 if($row.frameCount-ne$want[0] -or $row.sourceKeyframeIntervalTicks-ne$want[1] -or $row.playback-cne$want[2] -or $row.sourceKeyframeCount*$row.sourceKeyframeIntervalTicks-ne$row.frameCount){throw 'Reviewed animation timing differs.'}
 $hashes=New-Object 'Collections.Generic.List[string]'
 for($i=0;$i-lt$row.sourceKeyframeCount;$i++){
  $relative='sources/'+$id+'/key-'+$i.ToString('000')+'.png'
  $path=Join-Path $package ($relative.Replace('/',[IO.Path]::DirectorySeparatorChar));Ordinary $path
  $pin=$seal.files.PSObject.Properties[$relative].Value
  $bytes=[IO.File]::ReadAllBytes($path)
  if($bytes.Length-lt33 -or $bytes.Length-gt524288){throw 'Bounded source PNG required.'}
  $width=([int]$bytes[16]-shl24)-bor([int]$bytes[17]-shl16)-bor([int]$bytes[18]-shl8)-bor[int]$bytes[19]
  $height=([int]$bytes[20]-shl24)-bor([int]$bytes[21]-shl16)-bor([int]$bytes[22]-shl8)-bor[int]$bytes[23]
  if($width-ne192 -or $height-ne208 -or [BitConverter]::ToString($bytes,0,8)-cne'89-50-4E-47-0D-0A-1A-0A'){throw 'Source PNG identity or dimensions differ.'}
  $hasher=[Security.Cryptography.SHA256]::Create();try{$hash=([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}
  if($pin-cnotmatch'^[a-f0-9]{64}$' -or $hash-cne$pin){throw 'Immutable source key differs.'}
  $hashes.Add($hash);$blobs.Add($bytes);$total+=$bytes.Length
 }
 $descriptors.Add([ordered]@{id=$id;frameCount=[int]$row.frameCount;interval=[int]$row.sourceKeyframeIntervalTicks;once=($row.playback-ceq'once');keys=$hashes.ToArray()})
}
if($blobs.Count-ne86 -or $total-gt33554432){throw 'Reviewed key count or total resource bound differs.'}
$descriptor=[ordered]@{format='interlinked-violet-embedded-v1';manifestSha256='e7267f338b6ce53794f6c420451bb9be733f48e538cd429cc465c43205661c02';fps=30;animations=$descriptors.ToArray()}
$metadata=[Text.UTF8Encoding]::new($false).GetBytes(($descriptor|ConvertTo-Json -Depth 6 -Compress))
if($metadata.Length-gt32768){throw 'Animation descriptor exceeds bound.'}
$stream=[IO.File]::Open($output,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
try{$writer=[IO.BinaryWriter]::new($stream,[Text.UTF8Encoding]::new($false),$true);try{$writer.Write([Text.Encoding]::ASCII.GetBytes('ILVA1'));$writer.Write([int]$metadata.Length);$writer.Write($metadata);foreach($blob in $blobs){$writer.Write([int]$blob.Length);$writer.Write($blob)};$writer.Flush();$stream.Flush($true)}finally{$writer.Dispose()}}finally{$stream.Dispose()}
[pscustomobject]@{status='ANIMATION_RESOURCE_PACKED';path=$output;sha256=(Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant();animations=14;sourceKeys=86;sourcePngBytes=$total;runtimePrivatePathsIncluded=$false}|ConvertTo-Json
