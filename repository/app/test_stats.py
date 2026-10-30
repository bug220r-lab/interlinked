"""Isolated Stats lifecycle, registry, HTTP and native containment checks."""
from pathlib import Path
from contextlib import contextmanager
import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
import unittest
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('stats_gui_test',HERE/'interlinked_gui.py')
g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
h=g.load_companion('interlinked_hub');s=g.load_companion('interlinked_stats')
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
ROOT=args.output.resolve()/('stats-'+uuid.uuid4().hex[:8]);ROOT.mkdir(parents=True)

def until(fn):
    end=time.monotonic()+10
    while time.monotonic()<end:
        if fn():return
        time.sleep(.02)
    raise AssertionError('Condition timed out')

def library(data):
    data.mkdir(parents=True)
    with sqlite3.connect(data/'stats.sqlite3') as db:db.execute('CREATE TABLE sample(value)')
    for name in ('archive_registry','youtube_collections','shopify_collections','linktree_collections','tiktok_collections'):
        h.write_json(data/(name+'.json'),dict(archives=[],analysis_versions=[]) if name=='archive_registry' else dict(collections=[]))

class Tests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/uuid.uuid4().hex[:8];self.root.mkdir()
        self.data=self.root/'cache/stats-active/Files/Data';library(self.data)
        self.source=self.root/'source';(self.source/'scripts').mkdir(parents=True);(self.source/'.venv/Scripts').mkdir(parents=True)
        for name in ('app.py','scripts/launch.py','requirements.lock.txt','.venv/Scripts/python.exe'):(self.source/name).write_text('# fixture')
        self.leases=0
        self.engine=g.load_companion('interlinked_hub')
        test=self
        class Engine:
            @contextmanager
            def exclusive(self,path,create=False):
                if test.leases:raise ValueError('Held fixture cache lock')
                test.leases+=1
                try:yield
                finally:test.leases-=1
        class Hub:
            def original_id(self,key):
                if key!='stats-ui':raise ValueError('Unknown project')
                return 'stats-active'
            def lookup(self,key):
                self.original_id(key)
                return dict(originalId='stats-active',environmentHints=dict(STATS_DATA_DIR=dict(relativeToWorkingSet='Files/Data')))
            def folder_path(self,key):self.original_id(key);return test.data
            def invalidate(self):pass
            def refresh(self):pass
        class Controller:
            lock=threading.RLock();closing=False;transferring=False;hub=Hub();engine=Engine()
            r=type('Runtime',(),dict(state=self.root/'state',kit=self.root/'kit',cache=self.root/'cache',demo=False))()
            def _operation_running(self):return False
        self.c=Controller();self.c.r.state.mkdir();self.c.r.kit.mkdir()
        (self.data.parent.parent/'.client.lock').write_bytes(b'')
        self.opened=[]
        self.service=s.StatsService(self.c,g,tree_runner=self.runner,browser=self.opened.append)
        config=dict(format='interlinked-stats-connection-v1',sourceRoot=str(self.source),python=str(self.source/'.venv/Scripts/python.exe'),port=8501)
        for rel,key in (('scripts/launch.py','launcherSha256'),('.venv/Scripts/python.exe','pythonSha256'),('requirements.lock.txt','lockSha256')):config[key]=s.digest(self.source/rel)
        h.write_json(self.service.root/'connection.json',config)

    def runner(self,argv,**kw):
        self.assertEqual(kw['env']['STATS_DATA_DIR'],str(self.data))
        self.assertEqual(kw['env']['STATS_MODE'],'local')
        kw['on_started'](123)
        kw['stdout'].write(b'{"protocol":"stats-managed-v1","type":"ready","url":"http://127.0.0.1:8501"}\n')
        kw['on_poll'](1)
        self.assertEqual(json.loads(kw['stdin'].readline()),dict(action='stop'))
        kw['stdout'].write(b'{"protocol":"stats-managed-v1","type":"exit","clean":true}\n')
        kw['on_poll'](0)
        return 0

    def request(self,action,**kw):return self.service.request(dict(project='stats-ui',action=action,expectedRevision=self.service.revision,**kw))

    def test_lifecycle_independent_and_locked(self):
        self.c.active=dict(kind='mw5');self.request('start');until(lambda:self.service.status=='RUNNING')
        self.assertEqual(self.leases,1);self.assertTrue(self.service.blocks_quit())
        self.assertEqual(self.c.active,dict(kind='mw5'));self.assertFalse(self.c.transferring)
        for action in ('save','verify','release','start'):
            with self.assertRaises(ValueError):self.request(action,confirmation='stats-active')
        self.request('open-browser');self.assertEqual(len(self.opened),2)
        self.request('stop');self.service.thread.join(10)
        self.assertFalse(self.service.thread.is_alive());self.assertEqual(self.leases,0)
        self.assertEqual(self.service.status,'IDLE');self.assertFalse(self.service.blocks_quit())

    def test_missing_environment_and_tamper(self):
        (self.source/'.venv/Scripts/python.exe').unlink();self.assertFalse(self.service.view('stats-ui')['ready'])

    def test_registry_missing_escape_and_hash(self):
        path=self.data/'shopify_collections.json'
        for row in (dict(database='../escape'),dict(database='absent.sqlite3'),dict(database='stats.sqlite3',database_sha256='0'*64)):
            h.write_json(path,dict(collections=[row]))
            with self.assertRaises((ValueError,OSError)):s.validate_library(self.data,h)

    def test_bad_identity_revision_and_actions(self):
        for body in (dict(project='wrong',action='start',expectedRevision=0),dict(project='stats-ui',action='shell',expectedRevision=0),dict(project='stats-ui',action='start',expectedRevision=99),dict(project='stats-ui',action='start',expectedRevision=0,path='bad')):
            with self.assertRaises(ValueError):self.service.request(body)
        with self.assertRaises(ValueError):self.request('release',confirmation='yes')

    def test_interruption_requires_acknowledgement(self):
        self.service._journal('RUNNING',pid=123)
        recovered=s.StatsService(self.c,g,tree_runner=self.runner)
        self.assertTrue(recovered.blocks_quit())
        with self.assertRaises(ValueError):recovered.request(dict(project='stats-ui',action='start',expectedRevision=0))
        recovered.request(dict(project='stats-ui',action='acknowledge',expectedRevision=0,confirmation='stats-active'))
        self.assertEqual(recovered.status,'IDLE')

    def test_start_failure_preserves_lease_and_recovery(self):
        def fail(*a,**kw):kw['on_started'](123);raise OSError('fixture failure after containment')
        self.service.runner=fail;self.request('start');self.service.thread.join(10)
        self.assertEqual(self.leases,0);self.assertEqual(self.service.status,'RECOVERY_REQUIRED')

    def test_transfer_contention(self):
        self.c.transferring=True
        with self.assertRaises(ValueError):self.request('save')
        self.assertTrue(self.c.transferring)

    def test_native_tree_wait_and_pre_resume_failure(self):
        runner=g.load_companion('interlinked_stats_process').run_process_tree
        with open(self.root/'out','w+b') as out,open(self.root/'err','w+b') as err:
            began=time.monotonic()
            code=runner([sys.executable,'-c','import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.4)"])'],cwd=self.root,env=dict(os.environ),stdout=out,stderr=err)
            self.assertEqual(code,0);self.assertGreater(time.monotonic()-began,.35)
            marker=self.root/'must-not-run'
            def fail(pid):raise ValueError('before resume')
            with self.assertRaises(ValueError):runner([sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).touch()'],cwd=self.root,env=dict(os.environ),stdout=out,stderr=err,on_started=fail)
            self.assertFalse(marker.exists())


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,passed=result.wasSuccessful())))
    raise SystemExit(not result.wasSuccessful())
