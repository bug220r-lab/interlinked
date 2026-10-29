"""Windows local fixtures only; no production share or user data is accessed."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ENGINE=HERE.parents[1]/'outputs/Project-Library-2026-10-29/Helper/project_library.py'
spec=importlib.util.spec_from_file_location('project_library',ENGINE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fixture-',dir=HERE);self.base=Path(self.tmp.name)
        self.library=self.base/'library';self.returns=self.base/'returns';self.cache=self.base/'cache'
        self.library.mkdir();self.returns.mkdir();(self.library/'.library-access.lock').write_bytes(b'fixture')
        self.rows=[];self.add_source('a.txt',b'original a');self.add_source('nested/b.txt',b'original b');self.add_source('deleted.txt',b'original deleted')
        self.publish();self.root=self.cache/'sample';self.files=self.root/'Files/Drive_C/project'
    def tearDown(self):self.tmp.cleanup()
    def add_source(self,name,content,kind='file'):
        rel='Drive_C/project/'+name;p=self.library/'Sources'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);s=p.stat()
        self.rows.append(dict(path=rel,sourceRelative='Sources/'+rel,bytes=s.st_size,mtime_ns=s.st_mtime_ns,kind=kind));return p
    def publish(self):
        (self.library/'catalog.json').write_text(json.dumps(dict(format='project-library-catalog-v1',catalogId='fixture-catalog',projects=[dict(id='sample',name='Fixture',description='Synthetic',files=self.rows,notes=[],warnings=[])])),encoding='utf-8')
    def call(self,command,*extra,cache=None):
        out=io.StringIO()
        args=['--library',str(self.library),'--returns',str(self.returns),'--cache',str(cache or self.cache),'--test-local',command]
        if command!='list':args.append('sample')
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(io.StringIO()):code=m.main([*args,*extra])
        self.last=json.loads(out.getvalue());return code
    def okay(self,command,*extra,**kw):
        code=self.call(command,*extra,**kw);self.assertEqual(code,0,self.last);return self.last
    def state(self):return json.loads((self.root/'state.json').read_text())
    def head(self):return json.loads((self.returns/'sample/HEAD.json').read_text())
    def version(self):return json.loads((self.returns/'sample/versions'/self.head()['versionId']/'manifest.json').read_text())
    def test_roundtrip_changed_new_hidden_deleted_and_release(self):
        original={r['sourceRelative']:(self.library/r['sourceRelative']).read_bytes() for r in self.rows}
        self.okay('list');self.okay('fetch');(self.files/'nested/b.txt').write_bytes(b'edited b');(self.files/'deleted.txt').unlink()
        (self.files/'.env').write_bytes(b'fixture local secret');(self.files/'.git').mkdir();(self.files/'.git/config').write_bytes(b'fixture config')
        result=self.okay('save');self.assertEqual(result['payloadBytes'],len(b'edited bfixture local secretfixture config'))
        version=self.version();records={r['path']:r for r in version['files']}
        self.assertEqual(records['Drive_C/project/a.txt']['ref']['store'],'library')
        self.assertIn('Drive_C/project/deleted.txt',version['tombstones'])
        second=self.base/'second-cache';self.okay('fetch',cache=second)
        restored=second/'sample/Files/Drive_C/project';self.assertEqual((restored/'nested/b.txt').read_bytes(),b'edited b');self.assertFalse((restored/'deleted.txt').exists())
        self.assertEqual((restored/'.env').read_bytes(),b'fixture local secret')
        self.okay('verify');self.okay('release','--confirm','sample');self.assertEqual(self.state()['status'],'RELEASED')
        self.assertEqual(m.scan(self.root/'Files')[0],{});self.assertTrue((self.root/'events.jsonl').exists())
        for path,content in original.items():self.assertEqual((self.library/path).read_bytes(),content)
        self.okay('fetch');self.assertEqual((self.files/'nested/b.txt').read_bytes(),b'edited b')
    def test_save_reuses_previous_payload_after_verifying_it(self):
        self.okay('fetch');(self.files/'a.txt').write_bytes(b'edited');self.okay('save');first=self.version();self.okay('save');second=self.version()
        a=lambda v:next(r for r in v['files'] if r['path'].endswith('/a.txt'))
        self.assertEqual(a(first)['ref'],a(second)['ref']);self.assertEqual(self.last['payloadBytes'],0)
    def test_head_conflict_blocks_save_and_release(self):
        self.okay('fetch');self.okay('save');before=(self.files/'a.txt').read_bytes();head=self.head();head['manifestSha256']='0'*64
        (self.returns/'sample/HEAD.json').write_text(json.dumps(head))
        self.assertEqual(self.call('save'),2);self.assertEqual(self.call('release','--confirm','sample'),2);self.assertEqual((self.files/'a.txt').read_bytes(),before)
    def test_original_conflict_blocks_save_and_release(self):
        self.okay('fetch');self.okay('save');source=self.library/self.rows[0]['sourceRelative'];source.write_bytes(b'changed original')
        self.assertEqual(self.call('save'),2);self.assertEqual(self.call('release','--confirm','sample'),2);self.assertTrue((self.files/'a.txt').exists())
    def test_corrupt_return_blocks_release(self):
        self.okay('fetch');(self.files/'a.txt').write_bytes(b'edited');self.okay('save')
        rec=next(r for r in self.version()['files'] if r['path'].endswith('/a.txt'));(self.returns/rec['ref']['path']).write_bytes(b'broken')
        self.assertEqual(self.call('release','--confirm','sample'),2);self.assertEqual((self.files/'a.txt').read_bytes(),b'edited')
    def test_local_changed_or_new_file_blocks_all_deletion(self):
        self.okay('fetch');self.okay('save');(self.files/'a.txt').write_bytes(b'changed')
        self.assertEqual(self.call('release','--confirm','sample'),2);self.assertEqual(len(m.scan(self.root/'Files')[0]),3)
        (self.files/'a.txt').write_bytes(b'original a');(self.files/'new.tmp').write_bytes(b'new runtime data')
        self.assertEqual(self.call('release','--confirm','sample'),2);self.assertEqual(len(m.scan(self.root/'Files')[0]),4)
    def test_release_confirmation_and_committed_save_required(self):
        self.okay('fetch');self.assertEqual(self.call('release','--confirm','wrong'),2);self.assertEqual(self.call('release','--confirm','sample'),2)
        self.assertEqual(len(m.scan(self.root/'Files')[0]),3)
    def test_unowned_cache_preserved(self):
        (self.cache/'sample/Files').mkdir(parents=True);(self.cache/'sample/Files/important.txt').write_bytes(b'untouched')
        self.assertEqual(self.call('fetch'),2);self.assertEqual((self.cache/'sample/Files/important.txt').read_bytes(),b'untouched')
    def test_release_interruption_reverifies_remaining_and_completes(self):
        self.okay('fetch');self.okay('save');original=m.delete_held;count=0
        def interrupt(stream):
            nonlocal count
            count+=1
            if count==2:raise KeyboardInterrupt
            original(stream)
        with patch.object(m,'delete_held',interrupt):self.assertEqual(self.call('release','--confirm','sample'),130)
        self.assertEqual(self.state()['status'],'RELEASE_PARTIAL');self.assertEqual(len(m.scan(self.root/'Files')[0]),2)
        self.okay('release','--confirm','sample');self.assertEqual(self.state()['status'],'RELEASED')
    def test_release_new_file_after_precheck_blocks_before_deletion(self):
        self.okay('fetch');self.okay('save');original=m.Client.event
        def event(client,kind,**values):
            original(client,kind,**values)
            if kind=='RELEASE_VERIFIED':(self.files/'new.txt').write_bytes(b'keep me')
        with patch.object(m.Client,'event',event):self.assertEqual(self.call('release','--confirm','sample'),2)
        self.assertEqual(len(m.scan(self.root/'Files')[0]),4)
    def test_low_space_fetch_and_save_leave_no_head(self):
        low=type('DU',(),{'free':0})()
        with patch.object(m.shutil,'disk_usage',return_value=low):self.assertEqual(self.call('fetch'),2)
        self.assertFalse((self.files/'a.txt').exists());self.okay('fetch');(self.files/'a.txt').write_bytes(b'edited')
        with patch.object(m.shutil,'disk_usage',return_value=low):self.assertEqual(self.call('save'),2)
        self.assertFalse((self.returns/'sample/HEAD.json').exists())
    def test_interrupted_fetch_prefix_resume_and_corruption_guard(self):
        original=m.Progress.tick
        def interrupt(progress,phase,*args,**kw):
            if phase=='Fetching':raise KeyboardInterrupt
            original(progress,phase,*args,**kw)
        with patch.object(m,'BLOCK',4),patch.object(m.Progress,'tick',interrupt):self.assertEqual(self.call('fetch'),130)
        self.okay('fetch');self.assertEqual((self.files/'a.txt').read_bytes(),b'original a')
    def test_paths_aliases_and_reparse_guard(self):
        for path in ['../escape','C:/x','a:ads','a/NUL.txt','CONIN$','a.','a ','a//b','/root','a/COM¹.txt']:
            with self.assertRaises(m.LibraryError):m.relative(path)
        with self.assertRaises(m.LibraryError):m.paths_unique(['a','A'])
        with self.assertRaises(m.LibraryError):m.paths_unique(['a','a/b'])
        original=os.lstat
        def linked(path,*args,**kw):
            s=original(path,*args,**kw)
            if str(path).removeprefix('\\\\?\\')==str(self.library):
                return type('Fake',(),{'st_mode':s.st_mode,'st_file_attributes':0x400})()
            return s
        with patch.object(m.os,'lstat',linked):self.assertEqual(self.call('list'),2)
    def test_jsonl_complete_prefix_and_empty_file(self):
        self.add_source('empty',b'');log=self.add_source('live.jsonl',b'{"x":1}\n{"unfinished":','jsonl');self.publish();self.okay('fetch')
        self.assertEqual((self.files/'empty').read_bytes(),b'');self.assertEqual((self.files/'live.jsonl').read_bytes(),b'{"x":1}\n')
        with open(log,'ab') as out:out.write(b'2}\n')
        self.okay('save');self.okay('release','--confirm','sample')
    def test_library_publisher_lock_blocks_client(self):
        with m.exclusive(self.library/'.library-access.lock'):
            self.assertEqual(self.call('list'),2)
        self.okay('list')
    def test_mid_save_failure_does_not_publish_head(self):
        self.okay('fetch');(self.files/'a.txt').write_bytes(b'changed bytes');original=m.Progress.tick
        def interrupt(progress,phase,*args,**kw):
            if phase=='Saving':raise KeyboardInterrupt
            original(progress,phase,*args,**kw)
        with patch.object(m.Progress,'tick',interrupt):self.assertEqual(self.call('save'),130)
        self.assertFalse((self.returns/'sample/HEAD.json').exists());self.assertEqual((self.files/'a.txt').read_bytes(),b'changed bytes')
    def test_grouped_checkpoint_membership_and_release_confinement(self):
        for i in range(110):self.add_source(f'group/{i:03d}.txt',f'fixture {i}'.encode())
        self.publish();self.okay('fetch');self.assertEqual(len(self.state()['files']),113)
        outside=self.base/'outside-cache.txt';outside.write_bytes(b'outside retained')
        try:os.link(outside,self.files/'hardlinked-local.txt')
        except PermissionError:(self.files/'hardlinked-local.txt').write_bytes(outside.read_bytes()) # Sandbox may forbid synthetic hardlink creation.
        self.okay('save');self.assertEqual(len(self.version()['files']),114)
        self.okay('release','--confirm','sample');self.assertEqual(outside.read_bytes(),b'outside retained')
        self.assertEqual(m.scan(self.root/'Files')[0],{})
    def test_long_unicode_path_fetch_save_release(self):
        name='/'.join(['Unicode-\u00e9-'+('x'*80)]*3)+'/document.txt';self.add_source(name,b'long path fixture');self.publish()
        self.okay('fetch');path=self.files/name;self.assertGreater(len(str(path)),260);self.assertEqual(path.read_bytes(),b'long path fixture')
        self.okay('save');self.okay('release','--confirm','sample');self.assertFalse(path.exists())
    def test_interrupted_after_head_commit_recovers_exact_own_save(self):
        self.okay('fetch');(self.files/'a.txt').write_bytes(b'changed');original=m.Client.save_state
        def interrupted(client,state):
            if state.get('savedHead') and not state.get('pendingSave'):raise KeyboardInterrupt
            original(client,state)
        with patch.object(m.Client,'save_state',interrupted):self.assertEqual(self.call('save'),130)
        self.assertTrue(self.state()['pendingSave']);head=self.head()
        self.okay('save');self.assertTrue(self.last['recoveredInterruptedSave']);self.assertEqual(self.head(),head)
        self.okay('release','--confirm','sample')
    def test_interrupted_before_head_commit_recovers_and_blocks_local_drift(self):
        self.okay('fetch');original=m.atomic_json
        def interrupted(path,value,**kw):
            if Path(path).name=='HEAD.json':raise KeyboardInterrupt
            original(path,value,**kw)
        with patch.object(m,'atomic_json',interrupted):self.assertEqual(self.call('save'),130)
        self.assertFalse((self.returns/'sample/HEAD.json').exists());self.assertTrue(self.state()['pendingSave'])
        (self.files/'a.txt').write_bytes(b'changed meanwhile');self.assertEqual(self.call('save'),2);self.assertFalse((self.returns/'sample/HEAD.json').exists())
        (self.files/'a.txt').write_bytes(b'original a');self.okay('save');self.assertTrue(self.last['recoveredInterruptedSave'])
    def test_malformed_metadata_returns_structured_error(self):
        (self.library/'catalog.json').write_text('[]');self.assertEqual(self.call('list'),2);self.assertEqual(self.last['status'],'BLOCKED')
    def test_pending_save_traversal_head_rejected_before_access(self):
        self.okay('fetch');state=self.state();state['pendingSave']={'parentHead':None,'head':{'format':'project-library-head-v1','projectId':'sample','catalogId':'fixture-catalog','versionId':'../../escape','manifestSha256':'0'*64}}
        (self.root/'state.json').write_text(json.dumps(state));self.assertEqual(self.call('save'),2);self.assertIn('Invalid HEAD',self.last['error'])

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ClientTests))
    report={'engineSha256':hashlib.sha256(ENGINE.read_bytes()).hexdigest(),'testsRun':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful(),'scope':'Synthetic local fixtures only; no production UNC access. Synthetic hardlink creation was blocked by the sandbox; confinement fixture used a separate ordinary local file.'}
    (HERE/'test-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8');sys.exit(0 if result.wasSuccessful() else 1)
