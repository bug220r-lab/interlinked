#requires -Version 5.1
<# Creates only a fresh synthetic job-runner baseline. No production fetch,
catalog edit, task registration, network access or user project data. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$expectedSid='S-1-5-21-3086848077-1833151167-136462239-1001'
if($env:COMPUTERNAME -ine 'MSI' -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $expectedSid){throw 'Wrong machine/account.'}
$cacheBase='C:\Users\jlewi\Documents\Codex\MSI-Working-Projects'
$project='ssh-job-check-'+[guid]::NewGuid().ToString('N')
$cache=Join-Path $cacheBase $project
$files=Join-Path $cache 'Files'
$fixtureId=[guid]::NewGuid().ToString('N')
$utf8=New-Object Text.UTF8Encoding($false)
function Plain([string]$Path){
  $walk=[IO.Path]::GetFullPath($Path)
  while($walk){
    if(Test-Path -LiteralPath $walk){if(((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Linked path refused.'}}
    $parent=[IO.Directory]::GetParent($walk);$walk=if($parent){$parent.FullName}else{$null}
  }
}
function New-Text([string]$Path,[string]$Text){
  $data=$utf8.GetBytes($Text)
  $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$stream.Write($data,0,$data.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
function New-Json([string]$Path,$Value){New-Text $Path ($Value|ConvertTo-Json -Depth 12)}
Plain $cache
if(Test-Path -LiteralPath $cache){throw 'Fixture exists; refusing reuse.'}
New-Item -ItemType Directory -Path $files -ErrorAction Stop|Out-Null
$identity=@{projectId=$project;catalogId='msi-project-library-v1';library='\\MSI\ProjectLibrary\';returns='\\MSI\ProjectReturns\'}
$owner=@{format='project-library-owned-v1'}
$state=@{format='project-library-cache-v1';status='READY';files=@();originals=@();directories=@();baselineHead=$null;savedHead=$null}
foreach($key in $identity.Keys){$owner[$key]=$identity[$key];$state[$key]=$identity[$key]}
New-Json (Join-Path $cache 'OWNER.json') $owner
New-Json (Join-Path $cache 'state.json') $state
New-Json (Join-Path $cache 'SYNTHETIC-FIXTURE.json') @{format='msi-ssh-job-verification-fixture-v1';fixtureId=$fixtureId;projectId=$project;manualRunnerBaseline=$true;productionFetchPerformed=$false;productionDataUsed=$false;purpose='Actual THIS-MACHINE SSH launch through the unchanged MSI job helper; not a production project fetch.'}
New-Text (Join-Path $files 'input.txt') ('SYNTHETIC MSI SSH JOB CHECK '+$fixtureId+"`r`n")
$entry=@'
import datetime, hashlib, json, os, platform, time
from pathlib import Path

root = Path(__file__).absolute().parent
if platform.node().upper() != 'MSI' or Path.cwd() != root:
    raise RuntimeError('Unexpected test machine or working directory')
fixture = json.loads((root.parent / 'SYNTHETIC-FIXTURE.json').read_text(encoding='utf-8-sig'))
if fixture.get('format') != 'msi-ssh-job-verification-fixture-v1' or not fixture.get('manualRunnerBaseline'):
    raise RuntimeError('Synthetic fixture identity missing')
payload = (root / 'input.txt').read_bytes()
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
print('Synthetic MSI job started; waiting 12 seconds before producing its result.', flush=True)
time.sleep(12)
result = dict(format='msi-ssh-job-output-v1', fixtureId=fixture['fixtureId'],
              projectId=fixture['projectId'], computer=platform.node(),
              processId=os.getpid(), startedUtc=started,
              completedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              inputSha256=hashlib.sha256(payload).hexdigest(),
              sumOfSquares1Through10000=sum(n*n for n in range(1, 10001)),
              syntheticOnly=True)
with (root / 'job-result.json').open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())
print(json.dumps(result), flush=True)
'@
New-Text (Join-Path $files 'entry.py') $entry
New-Json (Join-Path $files 'spec.json') @{format='msi-job-spec-v1';projectId=$project;kind='python';entry='entry.py';workingDirectory='.';arguments=@();environmentPaths=@{}}
$manifest=@()
foreach($relative in @('OWNER.json','state.json','SYNTHETIC-FIXTURE.json','Files\input.txt','Files\entry.py','Files\spec.json')){
  $path=Join-Path $cache $relative
  $manifest += [pscustomobject]@{RelativePath=$relative;Bytes=(Get-Item -LiteralPath $path).Length;Sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$receipt=[ordered]@{Format='msi-ssh-job-fixture-preparation-v1';Status='PREPARED_NOT_STARTED';PreparedUtc=[DateTime]::UtcNow.ToString('o');FixtureId=$fixtureId;ProjectId=$project;CacheRoot=$cache;FilesRoot=$files;SpecFile=(Join-Path $files 'spec.json');ExpectedOutput=(Join-Path $files 'job-result.json');ExpectedSum=333383335000;ManualSyntheticRunnerBaseline=$true;ProductionFetchPerformed=$false;ProductionDataUsed=$false;Files=$manifest;TasksRegistered=$false}
$receiptPath=Join-Path $PSScriptRoot ('ssh-job-fixture-'+$fixtureId+'.json')
New-Json $receiptPath $receipt
[pscustomobject]@{Status=$receipt.Status;Receipt=$receiptPath;FixtureId=$fixtureId;ProjectId=$project;CacheRoot=$cache;SpecFile=$receipt.SpecFile}|ConvertTo-Json
