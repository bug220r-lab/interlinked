"""Synthetic, local-only tests. No production cache, SMB, MAGIX or settings."""
from pathlib import Path
import importlib.util
import json
import sys
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('candidate',HERE/'magix_projects.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
BASE=HERE.parents[1]/'magix-bootstrap-fix-2026-10-29/magix_workspace.py'
b=m.load_base(BASE)
ENGINE=HERE.parents[2]/'outputs/Project-Library-2026-10-29/Client/project_library.py'
e=b.load_engine(ENGINE)
root=HERE/'fixtures'/uuid.uuid4().hex
for name in ('kit','cache','library','returns','legacy/OneDrive'):(root/name).mkdir(parents=True,exist_ok=False)
(root/'library/.library-access.lock').write_bytes(b'')
checks=[]
def check(value,label):
    assert value,label
    checks.append(label)
def rejects(fn,label):
    try:fn()
    except Exception:checks.append(label)
    else:raise AssertionError('Expected rejection: '+label)

A=b.PROJECT;B='magix-fixture-b';C='magix-fixture-failed-fetch'
ENTRY=m.PREFIX+'OneDrive/Documents/shared project.MVP'
DELETE=m.PREFIX+'OneDrive/Documents/deleted.txt'
def rows_for(project):
    values={ENTRY:('MVP '+project).encode(),DELETE:b'intentional deletion'}
    for suffix in m.ROOTS:values[m.PREFIX+suffix+'/shared.bin']=('bytes for '+project+' '+suffix).encode()
    rows=[]
    for rel,data in values.items():
        path=root/'library/Sources'/project/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        rows.append(dict(path=rel,sourceRelative='Sources/'+project+'/'+rel,bytes=len(data),mtime_ns=path.stat().st_mtime_ns,kind='file'))
    return rows
catalog=[];index=[]
for project in (A,B,C):
    rows=rows_for(project)
    catalog.append(dict(id=project,name=project,files=rows,notes=[],warnings=[]))
    index.append(dict(id=project,name=project,category='magix-project',files=len(rows),bytes=sum(r['bytes'] for r in rows),entryPoints=[dict(path=ENTRY,kind='project')],aliasRoots=list(m.ROOTS),warnings=[],notes=[]))
e.atomic_json(root/'library/catalog.json',dict(format='project-library-catalog-v1',catalogId='msi-project-library-v1',projects=catalog),replace=False)
c=b.Config(root/'kit',root/'cache',root/'legacy',str(root/'library'),str(root/'returns'),sid=b.current_sid(),test_local=True,client_path=ENGINE,python_path=Path(sys.executable))
activity={'running':False};guard_calls=[]
a=b.Workspace(c,e,processes=lambda:[{'pid':1,'name':'Videodeluxe.exe'}] if activity['running'] else [])
manager=m.Projects(b,a,dict(format='magix-launcher-index-v1',catalogId=c.catalog,catalogPublishedUtc='fixture',projects=index),lambda:guard_calls.append(True))

# Establish the same old Stage1/Stage2 ownership, then RELEASE the anchor cache.
a.state_root(create=True);a.run_client('fetch')
info=a.j.create(c.link,c.target,lambda _:None)
stage1=dict(Format='magix-local-junction-owner-v1',Computer='THIS-MACHINE',ScriptSha256=b.STAGE1_SHA,OwnerSid=c.sid,RequestId=uuid.uuid4().hex,ProjectId=c.project,LegacyRoot=str(c.legacy),Link=str(c.link),Target=str(c.target),RootIdentity=a.j.directory_id(c.legacy),ParentIdentity=a.j.directory_id(c.parent),LinkIdentity=info['fileId'])
e.atomic_json(c.legacy/'.MAGIX-Local-Junction-Owner.json',stage1,replace=False)
a.migrate_stage1();a.save();a.release(A)
preserved={p:e.file_digest(p) for p in (c.legacy/'.MAGIX-Local-Junction-Owner.json',c.state/'installation.json',c.state/'stage1-owner-preserved.json')}
owner_before=(c.state/'OWNER.json').read_bytes()
with e.exclusive(c.state/'.workflow.lock'):
    rejects(manager.activate,'Old helper global lock blocks upgrade before owner mutation')
check((c.state/'OWNER.json').read_bytes()==owner_before,'Contended upgrade preserves exact owner')

atomic=e.atomic_json
def fail_owner(path,value,**kw):
    if path==c.state/'OWNER.json':raise OSError('fixture before marker commit')
    return atomic(path,value,**kw)
e.atomic_json=fail_owner
try:rejects(manager.activate,'Activation interruption before owner commit is recoverable')
finally:e.atomic_json=atomic
check((c.state/'OWNER-before-multiple-projects.bin').read_bytes()==owner_before,'Literal old marker is preserved before commit')
def fail_journal(path,value,**kw):
    if path==manager.journal:raise OSError('fixture after marker commit')
    return atomic(path,value,**kw)
e.atomic_json=fail_journal
try:rejects(manager.activate,'Activation interruption after marker commit is recoverable')
finally:e.atomic_json=atomic
manager.activate()
check(e.read_json(c.project_root/'state.json')['status']=='RELEASED','Activation supports released original anchor without refetch')
rejects(a.save,'Old single-project helper fails closed after protocol upgrade')
rejects(lambda:a.session(ENTRY,launch=lambda _:None),'Old Open refuses before fetching released anchor')
check(e.read_json(c.project_root/'state.json')['status']=='RELEASED','Old Open did not fetch or mutate anchor cache')

def prepare(project):
    selected=manager.selected(project)
    with manager.operation():selected.prepare()
    return selected
def bind(project):
    selected=manager.selected(project)
    with manager.operation(),e.exclusive(selected.c.project_root/'.client.lock'):return manager.bind(selected)

sa=prepare(A);afile=sa.c.project_root/'Files'/ENTRY;deleted=sa.c.project_root/'Files'/DELETE
afile.write_bytes(b'A edited working copy');deleted.unlink()
with manager.operation():sa.prepare()
check(afile.read_bytes()==b'A edited working copy' and not deleted.exists(),'READY preparation preserves edits and deletions')
# Offline reuse performs no UNC/source access and does not undo local changes.
offline=root/'library-offline';(root/'library').rename(offline)
try:
    with manager.operation():sa.prepare()
finally:offline.rename(root/'library')
check(afile.read_bytes()==b'A edited working copy' and not deleted.exists(),'READY reuse works while the fixture library is offline')
# Crash after an intermediate parent rename but before its READY ledger commit.
rename=manager.rename_directory
def fail_parent_commit(source,destination,identity):
    rename(source,destination,identity)
    if destination==c.legacy/'AppData':raise OSError('fixture after parent native rename')
manager.rename_directory=fail_parent_commit
try:rejects(lambda:bind(A),'Scaffolding interruption preserves native parent identity')
finally:manager.rename_directory=rename
bind(A)
check(e.read_json(c.state/'multi-parents.json')['parents']['AppData']['status']=='READY','Renamed parent reconciles by exact file ID')
check(len(manager.read_binding()['aliases'])==6,'All six actual native alias leaves bind together')
manager.check_all_links(manager.read_binding())
check((manager.link('OneDrive/Documents')/'shared project.MVP').read_bytes()==b'A edited working copy','Legacy reads reach selected A cache')
sb=prepare(B)
check(manager.read_binding()['status']=='UNBOUND','Switch fully unbinds old aliases before new fetch')
check(afile.read_bytes()==b'A edited working copy' and not deleted.exists(),'Switch preserves A edits and deletion')
bind(B)
check((manager.link('OneDrive/Documents')/'shared project.MVP').read_bytes()==('MVP '+B).encode(),'Same legacy path now reads B without contamination')
bfile=sb.c.project_root/'Files'/ENTRY;bfile.write_bytes(b'B independent edit')
prepare(A);bind(A)
check((manager.link('OneDrive/Documents')/'shared project.MVP').read_bytes()==b'A edited working copy' and bfile.read_bytes()==b'B independent edit','A to B to A retains both independent working copies')
rejects(sb.save,'Save selected B refuses aliases currently owned by A')
activity['running']=True
rejects(lambda:prepare(B),'Running MAGIX blocks switching before fetch')
activity['running']=False
with e.exclusive(c.state/'.workflow.lock'):
    rejects(lambda:prepare(B),'Shared workflow lock blocks concurrent project switch')
manager.recover()

# Recover an exact ordinary directory created before setting reparse metadata.
original_create=manager.j.create
def plain_partial(path,target,made):
    manager.private_directory(path);made(manager.j.directory_id(path))
    raise OSError('fixture before reparse metadata')
manager.j.create=plain_partial
try:rejects(lambda:bind(B),'Interrupted plain directory creation remains owned and journaled')
finally:manager.j.create=original_create
manager.recover()
check(all(not e.exists(manager.link(r)) for r in m.ROOTS),'Recover removes only exact empty partial junction object')

# A partial native create is recovered using the recorded project, not selection.
original_create=manager.j.create;count=[0]
def interrupted_create(path,target,made):
    count[0]+=1
    info=original_create(path,target,made)
    if count[0]==2:raise OSError('fixture after second junction creation')
    return info
manager.j.create=interrupted_create
try:rejects(lambda:bind(B),'Partial multiple-alias creation retains a recoverable journal')
finally:manager.j.create=original_create
check(manager.read_binding()['projectId']==B,'Interrupted binding records B as owner')
manager.recover()
check(manager.read_binding()['status']=='UNBOUND' and all(not e.exists(manager.link(r)) for r in m.ROOTS),'Recover removes only B recorded aliases, with no mixed set')

bind(A)
def fail_unbound(path,value,**kw):
    if path==manager.journal and value.get('status')=='UNBOUND':raise OSError('fixture after exact links removed')
    return atomic(path,value,**kw)
e.atomic_json=fail_unbound
try:rejects(manager.recover,'Unbind interrupted before final commit retains durable removal intent')
finally:e.atomic_json=atomic
manager.recover();check(manager.read_binding()['status']=='UNBOUND','Already removed aliases reconcile without touching cache data')

# A failed B-like fetch never copies originals over the previous working copy.
bind(A)
(root/'library'/catalog[2]['files'][0]['sourceRelative']).unlink()
rejects(lambda:prepare(C),'Missing source causes selected new fetch to fail clearly')
check(manager.read_binding()['status']=='UNBOUND' and afile.read_bytes()==b'A edited working copy','Failed new fetch leaves old aliases unbound and A edits intact')
failed=manager.selected(C)
check(e.read_json(failed.c.project_root/'state.json')['status']=='FETCHING','Interrupted fetch remains explicit FETCHING')
failed_source=root/'library'/catalog[2]['files'][0]['sourceRelative']
failed_source.write_bytes(('MVP '+C).encode())
prepare(C)
check(e.read_json(failed.c.project_root/'state.json')['status']=='READY','Frozen client resumes a previous FETCHING working set')

# Journal tampering may not select another cache, UNC path or unknown project.
bind(A);valid=e.read_json(manager.journal)
for label,change in (
 ('cross-project target',lambda v:v['aliases'][0].update(target=str(manager.target(B,m.ROOTS[0])))),
 ('unknown project',lambda v:v.update(projectId='foreign')),
 ('UNC target',lambda v:v['aliases'][0].update(target=r'\\MSI\ProjectLibrary')),
 ('ancestor alias',lambda v:v['aliases'][0].update(root='OneDrive'))):
    modified=json.loads(json.dumps(valid));change(modified);atomic(manager.journal,modified)
    rejects(manager.read_binding,'Reject altered '+label)
    atomic(manager.journal,valid)
manager.recover()

# A swapped leaf after preflight cannot become the new ownership authority.
bind(A);original_check=manager.check_all_links;swapped=[False];foreign=None
def swap_after_check(record):
    global foreign
    original_check(record)
    if record['status']=='UNBINDING' and not swapped[0]:
        row=record['aliases'][0];path=manager.link(row['root'])
        manager.j.unlink(path,row['junction'])
        foreign=manager.j.create(path,manager.target(B,row['root']),lambda _:None);swapped[0]=True
manager.check_all_links=swap_after_check
try:rejects(manager.recover,'Foreign leaf replacement after preflight is preserved')
finally:manager.check_all_links=original_check
check(manager.j.info(manager.link(m.ROOTS[0]))==foreign,'Foreign replacement junction remains unchanged')
# Fixture-only restore known original ownership so later independent cases run.
manager.j.unlink(manager.link(m.ROOTS[0]),foreign)
record=e.read_json(manager.journal);record['aliases'][0]['phase']='REMOVED';atomic(manager.journal,record);manager.recover()

sa.save();sa.verify()
rejects(lambda:sa.release(B),'Release requires selected A exact ID')
before_b=bfile.read_bytes();sa.release(A)
check(e.read_json(sa.c.project_root/'state.json')['status']=='RELEASED' and bfile.read_bytes()==before_b,'Verified release deletes only selected A cache, preserving B')
prepare(A)
check(afile.read_bytes()==b'A edited working copy' and not deleted.exists(),'Refetch of saved A restores edits and saved deletion')
pending=e.read_json(sa.c.project_root/'state.json');pending['pendingSave']={'fixture':True};atomic(sa.c.project_root/'state.json',pending)
rejects(lambda:prepare(A),'READY pendingSave blocks open without undoing edits')
del pending['pendingSave'];atomic(sa.c.project_root/'state.json',pending)
check(all(e.file_digest(p)==h for p,h in preserved.items()),'Installation and Stage1 baseline bytes remain unchanged')
check(bool(guard_calls),'Profile-unbound guard executes during transitions')

index=m.load_index(HERE/'MAGIX-All-Projects-Index.json',e)
check(len(index['projects'])==43 and sum(len(p['entryPoints']) for p in index['projects'])==276,'Pinned shipped index validates all 43 families and 276 entries')
result=dict(status='PASS',checks=checks,count=len(checks),engineSha256=e.file_digest(HERE/'magix_projects.py'),fixtureRoot=str(root),noProductionFiles=True,noNetwork=True,noApplicationLaunch=True)
(HERE/'test-results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
