"""Prepare a distinct reviewed retest plan. Local reads and new scratch writes only.
No SMB access, publication, task execution or modification of old evidence.
"""
from pathlib import Path
from datetime import datetime, timezone
import base64
import copy
import hashlib
import json

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1] / 'outputs/Project-Library-2026-10-29'
IDENTIFIER = '38ae56140ab74166bd23412973c606e0'
FIXTURE = HERE.parent / 'msi-server-conversion' / f'ssh-job-fixture-{IDENTIFIER}.json'
PREPARED = HERE / f'Prepared-{IDENTIFIER}'
OLD = HERE / 'Prepared'
CANDIDATE = HERE / 'repair-candidate/Invoke-SSH-Job-Verification.ps1'
VERIFY = PACKAGE / 'Server-Setup/Logs/VERIFY-20261029T221522-997495f8.json'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def sha(path):
    return digest(Path(path).read_bytes())

def read_json(path):
    return json.loads(Path(path).read_text('utf-8-sig'))

def new_bytes(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)

def encode(value):
    return (json.dumps(value, indent=2) + '\n').encode('utf-8')

def main():
    fixture = read_json(FIXTURE)
    assert fixture['FixtureId'] == IDENTIFIER
    assert fixture['ProjectId'] == 'ssh-job-check-125aa42cb3e843a3923bfda15a2b11b2'
    assert fixture['Status'] == 'PREPARED_NOT_STARTED' and not fixture['TasksRegistered']
    assert fixture['ManualSyntheticRunnerBaseline'] and not fixture['ProductionFetchPerformed'] and not fixture['ProductionDataUsed']
    assert not Path(fixture['ExpectedOutput']).exists()
    assert len(fixture['Files']) == 6
    for row in fixture['Files']:
        source = Path(fixture['CacheRoot']) / row['RelativePath']
        assert source.stat().st_size == row['Bytes'] and sha(source) == row['Sha256']
    old_bytes = (OLD / 'SSH-Verification-Plan.json').read_bytes()
    old_sidecar = (OLD / 'SSH-Verification-Plan.sha256').read_bytes()
    assert old_sidecar.decode('ascii').strip() == digest(old_bytes)
    prior = json.loads(old_bytes)
    assert prior['FixtureId'] == '41adcf9ad8c44a8fab897476b8fa4fbf'
    old_control = Path(prior['ControllerPath']).parent
    assert (old_control / 'SSH-Verification-Plan.json').read_bytes() == old_bytes
    assert (old_control / 'SSH-Verification-Plan.sha256').read_bytes() == old_sidecar
    assert sha(CANDIDATE) == '885f40e30bed9cfc886f31c40f66b45bf23f57095bfab71fc43aa4749023cc51'
    original = (old_control / 'Invoke-SSH-Job-Verification.ps1').read_text('utf-8-sig')
    expected = original.replace('@($task.Triggers).Count -eq 0', '@($task.Triggers | Where-Object { $null -ne $_ }).Count -eq 0')
    assert original.count('@($task.Triggers).Count -eq 0') == 1
    assert CANDIDATE.read_text('utf-8-sig') == expected
    assert sha(HERE / 'SSH-Verification-Common.ps1') == prior['CommonSha256']
    assert sha(HERE / 'Connect-To-MSI.ps1') == prior['ClientSha256']
    for row in prior['HelperFiles']:
        source = PACKAGE / row['RelativePath']
        assert source.stat().st_size == row['Bytes'] and sha(source) == row['Sha256']
    key = prior['HostPublicKey']
    assert sha(key['Evidence']['Path']) == key['Evidence']['Sha256']
    raw = base64.b64decode(key['Base64'], validate=True)
    assert key['Algorithm'] == 'ssh-ed25519' and len(raw) == 51
    assert key['Fingerprint'] == 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
    assert sha(VERIFY) == '6b3b39d8df5ad1cf938ac8eefb76c808cd20dec3b460d9dcaad91ae34d643b3c'
    verification = read_json(VERIFY)
    assert verification['Status'] == 'LOCAL_SERVER_VERIFIED_CLIENT_LOGIN_PENDING'
    assert len(verification['Checks']) == 19 and all(verification['Checks'].values())
    assert any(key['Fingerprint'] in item for item in verification['HostPublicKeyFingerprints'])
    name = 'SSH-Job-Verification-' + IDENTIFIER
    library = PACKAGE / 'HostLibrary' / name
    returns = PACKAGE / 'Returns' / name
    assert not library.exists() and not returns.exists()
    marker = {'Format': 'msi-ssh-job-verification-owner-v1', 'FixtureId': IDENTIFIER,
              'ProjectId': fixture['ProjectId'], 'ManualSyntheticRunnerBaseline': True,
              'ProductionFetchPerformed': False, 'ProductionDataUsed': False}
    marker_bytes = encode(marker)
    plan = copy.deepcopy(prior)
    plan.update(PreparedUtc=datetime.now(timezone.utc).isoformat(), FixtureId=IDENTIFIER,
                ProjectId=fixture['ProjectId'], CacheRoot=fixture['CacheRoot'], FixtureFiles=fixture['Files'],
                InputSha256=next(row['Sha256'] for row in fixture['Files'] if row['RelativePath'] == r'Files\input.txt'),
                FixturePreparationReceiptSha256=sha(FIXTURE),
                ControllerPath=str(library / 'Invoke-SSH-Job-Verification.ps1'),
                ControlUNC=r'\\MSI\ProjectLibrary' + '\\' + name,
                ReturnsUNC=r'\\MSI\ProjectReturns' + '\\' + name, ReturnsLocal=str(returns),
                ReturnsOwnerSha256=digest(marker_bytes), ControllerSha256=sha(CANDIDATE),
                ServerReadinessEvidence={'Path': str(VERIFY), 'Sha256': sha(VERIFY),
                    'Status': verification['Status'], 'ChecksPassed': 19,
                    'CompletedUtc': verification['FinishedUtc']})
    assert plan['HostPublicKey'] == prior['HostPublicKey'] and plan['HelperFiles'] == prior['HelperFiles']
    PREPARED.mkdir(exist_ok=False)
    new_bytes(PREPARED / 'FIXTURE-OWNER.json', marker_bytes)
    new_bytes(PREPARED / '.verification.lock', b'')
    plan_bytes = encode(plan)
    new_bytes(PREPARED / 'SSH-Verification-Plan.json', plan_bytes)
    new_bytes(PREPARED / 'SSH-Verification-Plan.sha256', (digest(plan_bytes) + '\n').encode('ascii'))
    rows = []
    def add(source, destination, *, old_data=None):
        data = source.read_bytes()
        row = dict(Source=str(source), Destination=str(destination), Bytes=len(data), Sha256=digest(data),
                   Policy='CREATE_NEW_ONLY' if old_data is None else 'REPLACE_ONLY_IF_HASH_MATCH')
        if old_data is not None:
            row.update(ExpectedExistingSha256=digest(old_data), ExpectedExistingBytes=len(old_data),
                       PreserveExistingAs=str(destination.with_name(destination.stem + '.before-' + IDENTIFIER + destination.suffix)))
        rows.append(row)
    add(CANDIDATE, library / 'Invoke-SSH-Job-Verification.ps1')
    add(HERE / 'SSH-Verification-Common.ps1', library / 'SSH-Verification-Common.ps1')
    for filename in ('SSH-Verification-Plan.json', 'SSH-Verification-Plan.sha256'):
        add(PREPARED / filename, library / filename)
    for filename in ('FIXTURE-OWNER.json', '.verification.lock'):
        add(PREPARED / filename, returns / filename)
    kit = Path(r'\\192.168.247.2\Users\Jude\Documents\MSI-Project-Library')
    add(PREPARED / 'SSH-Verification-Plan.json', kit / 'SSH-Verification-Plan.json', old_data=old_bytes)
    add(PREPARED / 'SSH-Verification-Plan.sha256', kit / 'SSH-Verification-Plan.sha256', old_data=old_sidecar)
    unchanged = []
    for filename in ('Connect-To-MSI.ps1', 'SSH-Verification-Common.ps1', 'Verify-MSI-SSH-Job.cmd', 'Open-MSI-Terminal.cmd'):
        source = HERE / filename
        unchanged.append(dict(Path=str(kit / filename), Sha256=sha(source), Bytes=source.stat().st_size))
    assert len(rows) == 8 and len({row['Destination'].casefold() for row in rows}) == 8
    manifest = dict(Format='msi-ssh-publication-manifest-v1', Status='PREPARED_NOT_PUBLISHED',
                    FixtureId=IDENTIFIER, PlanSha256=digest(plan_bytes), NewLibraryDirectory=str(library),
                    NewReturnsDirectory=str(returns), NewPcKit=str(kit), Rows=rows,
                    MustRemainUnchanged=unchanged, PriorFixtureId=prior['FixtureId'],
                    HistoricalEvidenceMustRemainUnchanged=True, SshRun=False, JobStarted=False)
    new_bytes(PREPARED / 'PUBLICATION-MANIFEST.json', encode(manifest))
    print(json.dumps(dict(Status=manifest['Status'], Directory=str(PREPARED), PlanSha256=digest(plan_bytes),
                         ManifestSha256=sha(PREPARED / 'PUBLICATION-MANIFEST.json'),
                         NewServerFiles=6, GuardedClientPlanReplacements=2, HostKeyUnchanged=True), indent=2))

if __name__ == '__main__':
    main()
