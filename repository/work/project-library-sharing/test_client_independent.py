"""Independent synthetic Windows client checks. No production shares are accessed."""
import contextlib, hashlib, importlib.util, io, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
ENGINE=BASE.parents[1]/'outputs/Project-Library-2026-10-29/Helper/project_library.py'
START_HASH=hashlib.sha256(ENGINE.read_bytes()).hexdigest()
spec=importlib.util.spec_from_file_location('independent_client',ENGINE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ROOT=Path(tempfile.mkdtemp(prefix='client-independent-',dir=BASE));RESULTS=[]

class Fixture:
 def __init__(self,name):
  self.base=ROOT/name;self.base.mkdir();self.library=self.base/'library';self.library.mkdir();self.returns=self.base/'returns';self.returns.mkdir();self.cache=self.base/'cache'
  (self.library/'.library-access.lock').write_bytes(b'fixture')
  self.original={};rows=[]
  for name,data,kind in [('empty',b'','file'),('notes.txt',b'untouched\n','file'),('nested/data.bin',bytes(range(128)),'file'),('live.jsonl',b'{"one":1}\n{"unfinished":','jsonl')]:
   rel='Drive_C/example/'+name;p=self.library/'Sources'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);self.original[p]=data
   rows.append(dict(path=rel,sourceRelative='Sources/'+rel,bytes=len(data),mtime_ns=p.stat().st_mtime_ns,kind=kind))
  self.catalog=dict(format='project-library-catalog-v1',catalogId='independent',projects=[dict(id='sample',files=rows,name='Synthetic',notes=[],warnings=[])])
  (self.library/'catalog.json').write_text(json.dumps(self.catalog))
  self.root=self.cache/'sample';self.files=self.root/'Files';self.last=None
 def call(self,command,*extra):
  out=io.StringIO();args=['--library',str(self.library),'--returns',str(self.returns),'--cache',str(self.cache),'--test-local',command]
  if command!='list':args.append('sample')
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(io.StringIO()):code=m.main([*args,*extra])
  self.last=json.loads(out.getvalue());return code
 def okay(self,command,*extra):
  code=self.call(command,*extra);assert code==0,(code,self.last);return self.last
 def state(self):return json.loads((self.root/'state.json').read_text())
 def head(self):return json.loads((self.returns/'sample/HEAD.json').read_text())
 def versionpath(self):return self.returns/'sample/versions'/self.head()['versionId']/'manifest.json'
 def release(self):return self.call('release','--confirm','sample')
 def leaves(self):return {k:p.read_bytes() for k,p in m.scan(self.files)[0].items()}
 def originals_intact(self):
  for p,data in self.original.items():assert p.read_bytes()==data

def run(name,fn):
 try:RESULTS.append(dict(test=name,status='PASS',detail=fn()))
 except BaseException as exc:RESULTS.append(dict(test=name,status='FAIL',error=repr(exc)))

def roundtrip():
 f=Fixture('roundtrip');f.okay('fetch');assert (f.files/'Drive_C/example/live.jsonl').read_bytes()==b'{"one":1}\n';assert (f.files/'Drive_C/example/empty').read_bytes()==b''
 (f.files/'Drive_C/example/notes.txt').write_bytes(b'changed\n');(f.files/'Drive_C/example/new-file').write_bytes(b'new')
 f.okay('save');f.okay('verify');assert f.release()==0;assert f.state()['status']=='RELEASED';assert f.leaves()=={};f.originals_intact()
 f.okay('fetch');assert (f.files/'Drive_C/example/notes.txt').read_bytes()==b'changed\n';assert (f.files/'Drive_C/example/new-file').read_bytes()==b'new';return 'Empty bytes, complete JSONL prefix, edits, save, verified release, and restored returned version all correct; originals unchanged.'

def preconditions():
 f=Fixture('preconditions');f.okay('fetch');before=f.leaves();assert f.release()==2;assert f.leaves()==before
 f.okay('save');assert f.call('release','--confirm','other')==2;assert f.leaves()==before
 (f.files/'Drive_C/example/notes.txt').write_bytes(b'local unsaved');before=f.leaves();assert f.release()==2;assert f.leaves()==before
 return 'No saved HEAD, wrong confirmation, and changed local content each preserve every file.'

def interrupted_revalidation():
 f=Fixture('interrupted');f.okay('fetch');f.okay('save');original=m.delete_held;count=0
 def stop(stream):
  nonlocal count
  count+=1
  if count==2:raise KeyboardInterrupt
  return original(stream)
 with patch.object(m,'delete_held',stop):assert f.release()==130
 assert f.state()['status']=='RELEASE_PARTIAL';remaining=f.leaves();assert len(remaining)==3
 path=next(iter(m.scan(f.files)[0].values()));saved=path.read_bytes();path.write_bytes(b'unsaved after interruption');changed=f.leaves();assert f.release()==2;assert f.leaves()==changed
 path.write_bytes(saved);assert f.release()==0;assert f.leaves()=={};f.originals_intact()
 return 'An interrupted partial release records state, refuses modified remaining bytes, then safely resumes after restored bytes.'

def corruption():
 f=Fixture('corruption');f.okay('fetch');(f.files/'Drive_C/example/notes.txt').write_bytes(b'returned');f.okay('save');before=f.leaves()
 version=json.loads(f.versionpath().read_text());row=next(r for r in version['files'] if r['ref']['store']=='returns');payload=f.returns/row['ref']['path'];payload.write_bytes(b'CORRUPT!')
 assert f.release()==2;assert f.leaves()==before
 return 'Corrupt remote returned payload blocks all local deletion.'

def unsafe_manifest():
 f=Fixture('malformed');f.okay('fetch');f.okay('save');before=f.leaves();hp=f.returns/'sample/HEAD.json';head=f.head();head['versionId']='../escape';hp.write_text(json.dumps(head));assert f.release()==2;assert f.leaves()==before
 head=f.state()['savedHead'];hp.write_text(json.dumps(head));vp=f.versionpath();version=json.loads(vp.read_text());version['files'][0]['path']='../escape';vp.write_text(json.dumps(version));head['manifestSha256']=hashlib.sha256(vp.read_bytes()).hexdigest();hp.write_text(json.dumps(head));assert f.release()==2;assert f.leaves()==before
 return 'Unsafe HEAD version IDs and hash-matching malformed manifest paths fail before release.'

def metadata_ownership():
 f=Fixture('ownership');f.okay('fetch');f.okay('save');before=f.leaves();marker=f.root/'OWNER.json';owner=json.loads(marker.read_text());owner['projectId']='another';marker.write_text(json.dumps(owner));assert f.release()==2;assert f.leaves()==before
 return 'An ownership mismatch blocks release without deleting local files.'

def active_handle():
 f=Fixture('active-handle');f.okay('fetch');f.okay('save');before=f.leaves();path=f.files/'Drive_C/example/notes.txt'
 with m.reader(path):assert f.release()==2
 assert f.leaves()==before;assert f.release()==0
 return 'An open local reader prevents acquiring delete rights, so no deletion occurs until the handle closes.'

def new_file_race():
 f=Fixture('newfile-race');f.okay('fetch');f.okay('save');original=m.Client.event
 def event(client,kind,**data):
  original(client,kind,**data)
  if kind=='RELEASE_VERIFIED':(f.files/'late-file').write_bytes(b'preserve newly created file')
 with patch.object(m.Client,'event',event):assert f.release()==2
 assert len(f.leaves())==5 and (f.files/'late-file').read_bytes()==b'preserve newly created file'
 return 'A file appearing after pre-verification blocks deletion at the second membership check.'

def native_handles():
 p=ROOT/'native-probe';p.write_bytes(b'x')
 with contextlib.ExitStack() as stack:
  streams=[stack.enter_context(m.reader(p)) for _ in range(9000)]
  assert all(m.fingerprint(s)['bytes']==1 for s in (streams[0],streams[8192],streams[-1]));assert streams[-1].read()==b'x'
 with m.reader(p,delete=True) as stream:assert stream.read()==b'x'
 return '9,000 simultaneous native readers pass beyond the old CRT limit; all handles close and exclusive access works afterward.'

def pending_recovery():
 for phase in ('before','after'):
  f=Fixture('save-recovery-'+phase);f.okay('fetch');(f.files/'Drive_C/example/notes.txt').write_bytes(b'changed before interrupted save');expected=f.leaves();original=m.atomic_json
  def interrupt(path,value,replace=True):
   if Path(path).name=='HEAD.json':
    if phase=='after':original(path,value,replace)
    raise KeyboardInterrupt
   return original(path,value,replace)
  with patch.object(m,'atomic_json',interrupt):assert f.call('save')==130
  assert f.state().get('pendingSave') is not None;assert f.leaves()==expected
  if phase=='before':assert f.call('verify')==2
  f.okay('save');assert f.last.get('recoveredInterruptedSave');assert f.state().get('pendingSave') is None;f.okay('verify');assert f.release()==0;f.originals_intact()
 return 'Interruption immediately before and after HEAD publication recovers exact pending save, verifies, and releases safely.'

def pending_conflict():
 f=Fixture('save-pending-conflict');f.okay('fetch');original=m.atomic_json
 def interrupt(path,value,replace=True):
  if Path(path).name=='HEAD.json':original(path,value,replace);raise KeyboardInterrupt
  return original(path,value,replace)
 with patch.object(m,'atomic_json',interrupt):assert f.call('save')==130
 (f.files/'Drive_C/example/notes.txt').write_bytes(b'unsaved after interrupted save');before=f.leaves();assert f.call('save')==2;assert f.release()==2;assert f.leaves()==before
 return 'Changed local bytes after interrupted save block reconciliation and release without removing files.'

def metadata_shapes():
 f=Fixture('metadata-shapes');cp=f.library/'catalog.json';raw=cp.read_bytes();cp.write_text('[]');assert f.call('list')==2;assert f.last['status']=='BLOCKED';cp.write_bytes(raw)
 f.okay('fetch');f.okay('save');before=f.leaves();hp=f.returns/'sample/HEAD.json';raw=hp.read_bytes()
 for text in ('[]','null','123'):
  hp.write_text(text);assert f.release()==2;assert f.last['status']=='BLOCKED';assert f.leaves()==before
 hp.write_bytes(raw);return 'List/null/scalar metadata receive structured BLOCKED results and preserve local files.'

for name,fn in [('roundtrip raw/JSONL/empty files',roundtrip),('release preconditions preserve files',preconditions),('interrupted release revalidates subset',interrupted_revalidation),('remote payload corruption',corruption),('malformed HEAD and manifest',unsafe_manifest),('cache ownership conflict',metadata_ownership),('in-use file handle',active_handle),('new-file race',new_file_race),('pending save commit recovery',pending_recovery),('pending save local conflict',pending_conflict),('metadata object shapes',metadata_shapes)]:run(name,fn)
if '--skip-native' not in sys.argv:run('native HANDLE scale',native_handles)
result=dict(engineSha256=START_HASH,engineSha256AtEnd=hashlib.sha256(ENGINE.read_bytes()).hexdigest(),fixtureRoot=str(ROOT),scope='Owned local synthetic data only; no network or production sources.',tests=RESULTS,passed=sum(r['status']=='PASS' for r in RESULTS),failed=sum(r['status']=='FAIL' for r in RESULTS))
(BASE/('client-independent-final-results.json' if '--skip-native' in sys.argv else 'client-independent-results.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));raise SystemExit(bool(result['failed']))
