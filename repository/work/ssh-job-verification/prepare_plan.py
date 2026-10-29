"""Prepare a blocked SSH verification plan. No publish, SSH, jobs or settings.

Root must replace the public-key/readiness placeholders with independently
verified MSI .pub evidence, set READY_FOR_ACTUAL_NEW_PC_TEST, then recompute the
plan sha256 sidecar before copying identical plans to the local kit/controller.
"""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
WORK=HERE.parent
PACKAGE=WORK.parent/'outputs/Project-Library-2026-10-29'
FIXTURE=WORK/'msi-server-conversion/ssh-job-fixture-41adcf9ad8c44a8fab897476b8fa4fbf.json'

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

if __name__=='__main__':
    fixture=json.loads(FIXTURE.read_text('utf-8-sig'))
    assert fixture['Status']=='PREPARED_NOT_STARTED'
    assert fixture['ManualSyntheticRunnerBaseline'] and not fixture['ProductionFetchPerformed'] and not fixture['ProductionDataUsed']
    assert len(fixture['Files']) == 6 and not Path(fixture['ExpectedOutput']).exists()
    for row in fixture['Files']:
        source=Path(fixture['CacheRoot'])/row['RelativePath']
        assert source.stat().st_size == row['Bytes'] and sha(source) == row['Sha256'], row['RelativePath']
    identifier=fixture['FixtureId'];name='SSH-Job-Verification-'+identifier
    prepared=HERE/'Prepared';prepared.mkdir(exist_ok=True)
    marker={'Format':'msi-ssh-job-verification-owner-v1','FixtureId':identifier,'ProjectId':fixture['ProjectId'],
            'ManualSyntheticRunnerBaseline':True,'ProductionFetchPerformed':False,'ProductionDataUsed':False}
    marker_bytes=(json.dumps(marker,indent=2)+'\n').encode()
    (prepared/'FIXTURE-OWNER.json').write_bytes(marker_bytes)
    (prepared/'.verification.lock').write_bytes(b'')
    helpers=[]
    for relative in ('Server-Setup/Start-MSIJob.ps1','Server-Setup/Invoke-MSIJob.ps1','Server-Setup/msi_job_runner.py','Server-Setup/msi_job_process.py','Helper/project_library.py'):
        file=PACKAGE/relative
        helpers.append({'RelativePath':relative,'Bytes':file.stat().st_size,'Sha256':sha(file)})
    plan={'Format':'msi-ssh-job-verification-v1','Status':'PREPARATION_ONLY_SERVER_READINESS_REQUIRED',
          'PreparedUtc':datetime.now(timezone.utc).isoformat(),'FixtureId':identifier,'ProjectId':fixture['ProjectId'],
          'ExpectedComputer':'MSI','ExpectedSid':'S-1-5-21-3086848077-1833151167-136462239-1001',
          'SshHost':'MSI','SshAddress':'192.168.247.1','SshUser':'jlewi',
          'CacheRoot':fixture['CacheRoot'],'FixtureFiles':fixture['Files'],'HelperFiles':helpers,
          'InputSha256':next(r['Sha256'] for r in fixture['Files'] if r['RelativePath']==r'Files\input.txt'),
          'FixturePreparationReceiptSha256':sha(FIXTURE),'ManualSyntheticRunnerBaseline':True,
          'ProductionFetchPerformed':False,'ProductionDataUsed':False,
          'ControllerPath':str(PACKAGE/'HostLibrary'/name/'Invoke-SSH-Job-Verification.ps1'),
          'ControlUNC':r'\\MSI\ProjectLibrary'+'\\'+name,'ReturnsUNC':r'\\MSI\ProjectReturns'+'\\'+name,
          'ReturnsLocal':str(PACKAGE/'Returns'/name),'ReturnsOwnerSha256':hashlib.sha256(marker_bytes).hexdigest(),
          'CommonSha256':sha(HERE/'SSH-Verification-Common.ps1'),
          'ControllerSha256':sha(HERE/'Invoke-SSH-Job-Verification.ps1'),
          'ClientSha256':sha(HERE/'Connect-To-MSI.ps1'),
          'HostPublicKey':{'Algorithm':'ssh-ed25519','Base64':None,'Fingerprint':None,
                           'TrustSource':'REQUIRES_MSI_LOCAL_PUBLIC_KEY_READ','Evidence':None},
          'ServerReadinessEvidence':None,'PrivateKeyFilesIncluded':False}
    encoded=(json.dumps(plan,indent=2)+'\n').encode()
    (prepared/'SSH-Verification-Plan.json').write_bytes(encoded)
    (prepared/'SSH-Verification-Plan.sha256').write_text(hashlib.sha256(encoded).hexdigest()+'\n',encoding='ascii')
    print(json.dumps({'status':plan['Status'],'plan':str(prepared/'SSH-Verification-Plan.json'),
                      'fixture':identifier,'commonSha256':plan['CommonSha256'],'controllerSha256':plan['ControllerSha256'],
                      'clientSha256':plan['ClientSha256']},indent=2))
