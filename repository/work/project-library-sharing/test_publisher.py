import argparse,contextlib,hashlib,importlib.util,io,json,os,sqlite3,tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1]
PATH=ROOT/'outputs/Project-Library-2026-10-29/Helper/publish_library.py'
BEFORE=hashlib.sha256(PATH.read_bytes()).hexdigest()
spec=importlib.util.spec_from_file_location('publisher_review',PATH);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
RUN=Path(tempfile.mkdtemp(prefix='publisher-fixtures-',dir=BASE));results=[]
def test(name,fn):
 try:detail=fn();results.append({'test':name,'status':'PASS','detail':detail})
 except Exception as e:results.append({'test':name,'status':'FAIL','error':repr(e)})
def case(name):p=RUN/name;p.mkdir();return p
def catalog(c,source):
 s=source.stat();rel='Drive_C/'+'/'.join(source.parts[1:]);row=dict(source=str(source),path=rel,bytes=s.st_size,mtime_ns=s.st_mtime_ns,kind='file')
 d=dict(format=m.FORMAT,catalogId=m.CATALOG_ID,publicationStatus='SOURCE_CATALOG_PREPARED',publishingSources=[row],projects=[dict(id='fixture',name='Fixture',description='',files=[dict(path=rel,sourceRelative='Sources/'+rel,bytes=s.st_size,mtime_ns=s.st_mtime_ns,kind='file')],notes=[],warnings=[])],summary={},deferred=[],exclusions=[])
 p=c/'catalog-source.json';p.write_text(json.dumps(d));return p,rel
def publish(p,out,refresh=False):
 with contextlib.redirect_stdout(io.StringIO()):return m.run(argparse.Namespace(source_catalog=str(p),output=str(out),dry_run=False,refresh=refresh))
def expect(fn):
 try:fn()
 except (m.UnsafePublication,RuntimeError,OSError):return
 raise AssertionError('Expected refusal')
def hardlinks():
 c=case('hardlinks');s=c/'original.bin';s.write_bytes(b'raw source bytes');p,rel=catalog(c,s);out=c/'host';before=s.stat();h=hashlib.sha256(s.read_bytes()).hexdigest();publish(p,out);t=out/'Sources'/rel
 assert m.identity(m.file_identity(s))==m.identity(m.file_identity(t));assert s.read_bytes()==t.read_bytes();assert hashlib.sha256(s.read_bytes()).hexdigest()==h
 after=s.stat();assert before.st_mtime_ns==after.st_mtime_ns and before.st_file_attributes==after.st_file_attributes
 pub=json.loads((out/'catalog.json').read_text());assert pub['summary']['publishedUniqueFiles']==1 and pub['summary']['publishedLogicalBytes']==len(s.read_bytes());assert pub['projects'][0]['summary']['files']==1
 publish(p,out);assert m.identity(m.file_identity(s))==m.identity(m.file_identity(t))
 return 'Ordinary payload is same NTFS file identity, source bytes/mtime/attributes unchanged, repeat publication and catalog metrics correct.'
def replacement():
 c=case('replacement');s=c/'original.bin';s.write_bytes(b'old');p,rel=catalog(c,s);out=c/'host';publish(p,out);t=out/'Sources'/rel
 newer=c/'new.bin';newer.write_bytes(b'new');os.replace(newer,s);expect(lambda:publish(p,out));assert t.read_bytes()==b'old'
 publish(p,out,True);assert t.read_bytes()==b'new';retired=list((out/'.retired').rglob('original.bin'));assert retired and retired[0].read_bytes()==b'old'
 return 'Replaced source identity requires explicit refresh; refresh retains prior published bytes.'
def unowned():
 c=case('unowned');s=c/'source';s.write_bytes(b'x');p,rel=catalog(c,s);out=c/'host';out.mkdir();(out/'user-file').write_bytes(b'preserve')
 before=set(x.name for x in out.iterdir());expect(lambda:publish(p,out));assert (out/'user-file').read_bytes()==b'preserve';assert set(x.name for x in out.iterdir())==before
 return 'Nonempty unowned folder rejected without adding metadata or changing its files.'
def paths():
 for p in ['../bad','/absolute','bad:stream','CONIN$','COM¹.txt','LPT²','trailing./file']:
  expect(lambda p=p:m.relative(p))
 expect(lambda:m.clean_path(Path(r'C:\Users\jlewi\Documents\My Music')))
 return 'Traversal, Windows aliases and known junction refused by metadata checks.'
def sqlite_snapshot():
 c=case('sqlite');s=c/'active.sqlite3';dst=c/'snapshot.sqlite3';conn=sqlite3.connect(s)
 try:
  conn.execute('PRAGMA journal_mode=WAL');conn.execute('CREATE TABLE t(v)');conn.execute('INSERT INTO t VALUES (1)');conn.commit();conn.execute('BEGIN IMMEDIATE');conn.execute('INSERT INTO t VALUES (2)')
  receipt=m.snapshot_database(s,dst)
  with contextlib.closing(sqlite3.connect(dst)) as q:assert q.execute('SELECT v FROM t').fetchall()==[(1,)];assert q.execute('PRAGMA integrity_check').fetchone()==('ok',)
  assert receipt['sha256']==hashlib.sha256(dst.read_bytes()).hexdigest();conn.rollback();assert conn.execute('SELECT v FROM t').fetchall()==[(1,)]
 finally:conn.close()
 return 'Live WAL snapshot includes committed data only; source transaction and data preserved.'
def lock_boundary():
 c=case('locking');s=c/'source';s.write_bytes(b'x');p,rel=catalog(c,s);out=c/'host';publish(p,out)
 with m.publish_lock(out):expect(lambda:publish(p,out))
 publish(p,out)
 return 'Exclusive publication lock blocks a concurrent publisher and later permits normal publication.'
def mapping():
 c=case('mapping');s=c/'source';s.write_bytes(b'x');p,rel=catalog(c,s);d=json.loads(p.read_text());d['publishingSources'][0]['path']='Drive_C/unrelated';p.write_text(json.dumps(d));expect(lambda:m.validate_catalog(p))
 return 'Incorrect source-to-Drive_C mapping rejected.'
test('hardlink identity and metrics',hardlinks);test('replacement and retained refresh',replacement);test('unowned output refusal',unowned);test('path and reparse guards',paths);test('SQLite committed snapshot',sqlite_snapshot);test('exclusive publication lock',lock_boundary);test('exact source mapping',mapping)
out={'publisherSha256':BEFORE,'publisherSha256AtEnd':hashlib.sha256(PATH.read_bytes()).hexdigest(),'fixtureRoot':str(RUN),'tests':results,'passed':sum(x['status']=='PASS' for x in results),'failed':sum(x['status']=='FAIL' for x in results),'scope':'Only owned synthetic local sources and metadata of known Windows junction; no production publication or network access.'}
(BASE/'publisher-test-results.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2));raise SystemExit(bool(out['failed']))
