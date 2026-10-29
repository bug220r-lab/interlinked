"""Creates a synthetic staging fixture under work only; never publishes shares."""
import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('smoke_runner', HERE/'verify_new_pc_workflow.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

def build(base, fixture=None):
    base = Path(base)
    base.mkdir(exist_ok=False)
    fixture = fixture or ('Verification-New-PC-'+uuid.uuid4().hex)
    library = base/'Library'; returns = base/'Returns'
    library.mkdir(); returns.mkdir()
    owner = {'format':'project-library-verification-fixture-v1','fixtureName':fixture,
             'catalogId':fixture,'projectId':runner.PROJECT,'syntheticOnly':True}
    encoded = (json.dumps(owner,indent=2)+'\n').encode('utf-8')
    note = ('ISOLATED NEW-PC VERIFICATION FIXTURE. Synthetic bytes only.\n'
            'Not part of the production project catalog. Retain versions and receipts.\n')
    for folder in (library, returns):
        (folder/'FIXTURE-OWNER.json').write_bytes(encoded)
        (folder/'FIXTURE-README.txt').write_text(note,encoding='utf-8')
    (library/'.library-access.lock').write_bytes(b'')
    (returns/'.verification-run.lock').write_bytes(b'')
    rows=[]
    for leaf,payload in runner.SOURCES.items():
        relative=runner.PREFIX+leaf
        source=library/'Sources'/relative
        source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(payload)
        rows.append({'path':relative,'sourceRelative':'Sources/'+relative,'bytes':len(payload),
                     'mtime_ns':source.stat().st_mtime_ns,'kind':'jsonl' if leaf.endswith('.jsonl') else 'file'})
    catalog={'format':'project-library-catalog-v1','catalogId':fixture,'projects':[
        {'id':runner.PROJECT,'name':'New-PC synthetic workflow verification','description':'Five small synthetic files; no user projects.',
         'files':rows,'notes':['Isolated smoke fixture; not in the production catalog.'],'warnings':[]}]}
    (library/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n',encoding='utf-8')
    plan={'format':'project-library-new-pc-verification-v1','fixtureName':fixture,'catalogId':fixture,
          'projectId':runner.PROJECT,'preparedUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'catalogSha256':runner.sha((library/'catalog.json').read_bytes()),'ownerSha256':runner.sha(encoded),
          'sourceHashes':{runner.PREFIX+k:runner.sha(v) for k,v in runner.SOURCES.items()},
          'sourceBytes':sum(map(len,runner.SOURCES.values()))}
    (base/'New-PC-Verification-Plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    return plan,library,returns

if __name__=='__main__':
    name='Verification-New-PC-'+uuid.uuid4().hex
    staging=HERE/'Prepared'/name;staging.parent.mkdir(exist_ok=True)
    plan,library,returns=build(staging,name)
    inventory=[]
    for side in ('Library','Returns'):
        for file in sorted((staging/side).rglob('*')):
            if file.is_file():inventory.append({'path':file.relative_to(staging).as_posix(),'bytes':file.stat().st_size,'sha256':runner.sha(file.read_bytes())})
    receipt={'status':'PREPARED_ONLY_NOT_PUBLISHED','fixtureName':name,'staging':str(staging),
             'intendedLibraryUNC':'\\\\MSI\\ProjectLibrary\\'+name,
             'intendedReturnsUNC':'\\\\MSI\\ProjectReturns\\'+name,'files':inventory}
    (staging/'STAGING-RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,indent=2))
