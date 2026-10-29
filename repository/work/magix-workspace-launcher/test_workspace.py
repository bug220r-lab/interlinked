"""Only bounded synthetic local fixtures; no production settings, jobs or UI."""
from pathlib import Path
import importlib.util
import json
import sys
import threading
import time
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('workspace_under_test',HERE/'magix_workspace.py')
w=importlib.util.module_from_spec(spec);sys.modules[spec.name]=w;spec.loader.exec_module(w)
engine_path=HERE.parents[1]/'outputs/Project-Library-2026-10-29/Client/project_library.py'
e=w.load_engine(engine_path)
root=HERE/('Fixture-'+uuid.uuid4().hex)
for name in ('kit','library','returns','cache','legacy/OneDrive'):(root/name).mkdir(parents=True,exist_ok=False)
(root/'library/.library-access.lock').write_bytes(b'')
source={w.DEFAULT_MVP:b'synthetic MVP initial\0',w.MEDIA:b'synthetic local movie\0'*30,w.DOCS+'/intentional-delete.txt':b'delete me as an edit\n'}
rows=[]
for rel,data in source.items():
    path=root/'library/Sources'/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    rows.append(dict(path=rel,sourceRelative='Sources/'+rel,bytes=len(data),mtime_ns=path.stat().st_mtime_ns,kind='file'))
(root/'library/catalog.json').write_text(json.dumps(dict(format='project-library-catalog-v1',catalogId='msi-project-library-v1',projects=[dict(id=w.PROJECT,name='Synthetic MAGIX only',files=rows,notes=[],warnings=[])])))
config=w.Config(root/'kit',root/'cache',root/'legacy',str(root/'library'),str(root/'returns'),sid=w.current_sid(),test_local=True,client_path=engine_path,python_path=Path(sys.executable))
activity={'running':False}
ws=w.Workspace(config,e,processes=lambda:[{'pid':123,'name':'Videodeluxe.exe'}] if activity['running'] else [])
checks=[]
def check(value,label):
    assert value,label
    checks.append(label)
def rejects(action,label):
    try:action()
    except Exception:checks.append(label)
    else:raise AssertionError('Expected rejection: '+label)

# Frozen CLI creates a real owned READY fixture cache.
ws.state_root(create=True)
ws.run_client('fetch')
info=ws.j.create(config.link,config.target,lambda _:None)
stage1=dict(Format='magix-local-junction-owner-v1',Computer='THIS-MACHINE',ScriptSha256=w.STAGE1_SHA,OwnerSid=config.sid,RequestId=uuid.uuid4().hex,ProjectId=config.project,LegacyRoot=str(config.legacy),Link=str(config.link),Target=str(config.target),RootIdentity=ws.j.directory_id(config.legacy),ParentIdentity=ws.j.directory_id(config.parent),LinkIdentity=info['fileId'])
e.atomic_json(config.legacy/'.MAGIX-Local-Junction-Owner.json',stage1,replace=False)
stage1_sha=e.file_digest(config.legacy/'.MAGIX-Local-Junction-Owner.json')
e.atomic_json(config.legacy/'.MAGIX-Local-Junction-Owner.json',dict(stage1,ScriptSha256='0'*64))
rejects(ws.migrate_stage1,'Unreviewed Stage1 marker helper hash cannot be adopted')
e.atomic_json(config.legacy/'.MAGIX-Local-Junction-Owner.json',stage1)
original_atomic=e.atomic_json
def interrupt_adoption(path,value,**kwargs):
    if path==config.state/'binding.json':raise OSError('synthetic interruption after installation commit')
    return original_atomic(path,value,**kwargs)
e.atomic_json=interrupt_adoption
try:rejects(ws.migrate_stage1,'Adoption interruption after installation commit is recorded')
finally:e.atomic_json=original_atomic
adopted=ws.migrate_stage1()
check(e.read_json(config.state/'binding.json')['status']=='BOUND','Exact interrupted Stage1 adoption resumes without overwriting evidence')
check(adopted['stage1Preserved'] and e.file_digest(config.legacy/'.MAGIX-Local-Junction-Owner.json')==stage1_sha,'Exact Stage1 adoption preserves original owner receipt')

# Real edits/deletions must survive READY preparation and rebind.
mvp=config.project_root/'Files'/w.DEFAULT_MVP;mvp.write_bytes(b'legitimate edited project\0')
deleted=config.project_root/'Files'/w.DOCS/'intentional-delete.txt';deleted.unlink()
ws.prepare()
check(mvp.read_bytes()==b'legitimate edited project\0' and not deleted.exists(),'READY edits and deletions are preserved without fetching')
media=config.project_root/'Files'/w.MEDIA;media_bytes=media.read_bytes();media.unlink()
rejects(lambda:ws.selected_file(w.DEFAULT_MVP),'Missing requested READY media blocks without restoring deletion')
check(not media.exists(),'Missing READY media is not silently restored')
media.write_bytes(media_bytes)

# Save invokes unchanged CLI; explicit deletion becomes part of the saved version.
ws.save()
check(not e.exists(config.link),'Save unbinds exact alias before frozen CLI')
check(not deleted.exists() and mvp.read_bytes()==b'legitimate edited project\0','Save and Verify preserve edited project and tombstone')
ws.bind();check(e.exists(config.link),'Rebind after legitimate edits does not require original MVP hash')
activity['running']=True
rejects(ws.unbind,'Running MAGIX blocks junction removal')
rejects(ws.save,'Running MAGIX blocks save lifecycle')
activity['running']=False
rejects(lambda:ws.release('YES'),'Release requires exact typed project ID')

# Explicit Done waits for an observed app despite a disappearing shell bootstrap.
calls=[]
def launch(_):
    def delayed():
        time.sleep(0.4);activity['running']=True
    threading.Thread(target=delayed,daemon=True).start()
def prompt(_):
    calls.append(1)
    rejects(lambda: _cache_lock(), 'Client cache lock held across session/startup prompt '+str(len(calls)))
    if len(calls)==1:raise KeyboardInterrupt() # must retain protection
    if len(calls)==2:return 'DONE' # before MAGIX appeared: must keep waiting
    time.sleep(0.9);activity['running']=False;return 'DONE'
def _cache_lock():
    with e.exclusive(config.project_root/'.client.lock'): pass
ws.session(prompt=prompt,launch=launch)
check(len(calls)>=3 and not e.exists(config.link),'Ctrl+C and early Done retain lock across Steam startup until observed app closes')

# Reporting/input and process-enumeration failures must retain the cache lock.
observations=[]
def failing_processes():
    if activity.get('inspectError'):raise OSError('synthetic enumeration failure')
    return [{'pid':123,'name':'Videodeluxe.exe'}] if activity['running'] else []
ws.processes=failing_processes
def launch_error_case(_):activity.update(running=True,inspectError=True)
def failed_input(_):
    def close_later():
        time.sleep(0.5)
        try:_cache_lock()
        except Exception:observations.append('locked_during_inspection_failure')
        activity['inspectError']=False
        time.sleep(0.3);activity['running']=False
    threading.Thread(target=close_later,daemon=True).start()
    raise EOFError('synthetic input closed')
rejects(lambda:ws.session(prompt=failed_input,launch=launch_error_case),'Input/enumeration failure does not claim session completion')
check(observations==['locked_during_inspection_failure'],'Session protection retained through inspection failure until app closes')
ws.recover()

ws.bind()
released=ws.release(config.project)
check(released['status']=='RELEASED' and not e.exists(config.link),'Explicit release unbinds first and uses frozen verified deletion')
ws.prepare()
check(mvp.read_bytes()==b'legitimate edited project\0' and not deleted.exists(),'Opening preparation after RELEASED refetches saved edits and deletions')
ws.bind()

# Current remote original conflict must still block a save and preserve local data.
original=root/'library/Sources'/w.MEDIA;old=original.read_bytes();original.write_bytes(b'conflicting source')
rejects(ws.save,'Changed original source blocks frozen Save without losing local edits')
check(mvp.read_bytes()==b'legitimate edited project\0','Conflict preserves local edited project')
original.write_bytes(old)

head_path=root/'returns'/config.project/'HEAD.json';head=e.read_json(head_path)
e.atomic_json(head_path,dict(head,versionId=uuid.uuid4().hex))
try:rejects(ws.save,'Changed remote HEAD blocks save without merging or replacing edits')
finally:e.atomic_json(head_path,head)

# Exact metadata prevents substitution of the active alias.
ws.bind();record=e.read_json(config.state/'binding.json');wrong=dict(record['junction'],fileId='wrong')
rejects(lambda:ws.j.unlink(config.link,wrong),'Wrong owned junction identity cannot remove link')
def interrupt_unbind(path,value,**kwargs):
    if path==config.state/'binding.json' and value.get('status')=='UNBOUND':raise OSError('synthetic crash after exact unlink')
    return original_atomic(path,value,**kwargs)
e.atomic_json=interrupt_unbind
try:rejects(ws.recover,'Crash after unlink retains durable UNBINDING intent')
finally:e.atomic_json=original_atomic
check(not e.exists(config.link) and e.read_json(config.state/'binding.json')['status']=='UNBINDING','Unlink was exact and metadata remains recoverable')
ws.recover();check(not e.exists(config.link) and media.read_bytes()==media_bytes,'Recovery reconciles absent owned link and keeps all local files')
record=dict(status='BINDING',projectId=config.project,target=str(config.target),createdId=None)
e.atomic_json(config.state/'binding.json',record)
def made(identity):record['createdId']=identity;e.atomic_json(config.state/'binding.json',record)
ws.j.create(config.link,config.target,made)
check(ws.binding()['status']=='BOUND','Orphan completed junction commit is recovered from exact durable creation identity')
ws.recover()
check(ws.setup()['status']=='SETUP_READY','Repeated setup reuses verified ownership without UAC when normal rights suffice')
cancelled=ws.session(prompt=lambda _: 'CANCEL STARTUP',launch=lambda _:None)
check(cancelled['status']=='STARTUP_CANCELLED_LOCAL_DATA_RETAINED' and not e.exists(config.link) and mvp.exists(),'Never-observed startup can be explicitly cancelled without saving or releasing local data')

result=dict(status='PASS',checks=checks,count=len(checks),helperSha256=w.hash_bytes((HERE/'magix_workspace.py').read_bytes()),fixture=str(root),networkAccess=False,productionDataUsed=False,magixLaunched=False,uacRequested=False)
e.atomic_json(HERE/('test-results-'+uuid.uuid4().hex+'.json'),result,replace=False)
print(json.dumps(result,indent=2))
