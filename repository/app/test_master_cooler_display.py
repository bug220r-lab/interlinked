"""Private cooler metadata fixtures; synthetic SQLite, no real chats or USB."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('master_cooler_fixture', Path(__file__).with_name('interlinked_master.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CoolerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-private-cooler-')
        self.root = Path(self.temp.name)
        self.database = self.root / 'metadata.sqlite'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,name TEXT,updated_at INTEGER,archived INTEGER,first_user_message TEXT,preview TEXT,rollout_path TEXT,title TEXT DEFAULT 'PRIVATE INITIAL PROMPT')")
            db.executemany('INSERT INTO threads(id,name,updated_at,archived,first_user_message,preview,rollout_path) VALUES(?,?,1,?,?,?,?)', [
                ('active', 'Active\n  chat\u202e ' + 'x'*200, 0, 'PRIVATE PROMPT', 'PRIVATE PREVIEW', 'PRIVATE PATH'),
                ('archived', 'ARCHIVED SECRET', 1, 'PRIVATE PROMPT', 'PRIVATE PREVIEW', 'PRIVATE PATH'),
                ('unrequested', 'UNREQUESTED SECRET', 0, 'PRIVATE PROMPT', 'PRIVATE PREVIEW', 'PRIVATE PATH')])
        self.resolver = m.CoolerTitleResolver(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def node(self, session):
        return m.lifecycle_node_id('main', session)

    def test_exact_id_bounded_title_and_no_payload_columns(self):
        before = self.database.read_bytes()
        value = self.resolver.resolve({self.node('active'): 'active', self.node('archived'): 'archived'}, now=1)
        self.assertEqual(list(value), [self.node('active')])
        self.assertTrue(value[self.node('active')].startswith('Active chat '))
        self.assertEqual(len(value[self.node('active')]), 160)
        for secret in ('PRIVATE', 'UNREQUESTED SECRET', 'ARCHIVED SECRET', '\u202e', '\n'):
            self.assertNotIn(secret, json.dumps(value, ensure_ascii=False))
        self.assertEqual(before, self.database.read_bytes())

    def test_cache_is_ten_seconds_and_drops_unrequested_ids(self):
        candidates = {self.node('active'): 'active'}
        self.resolver.resolve(candidates, now=1)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE threads SET name=? WHERE id=?', ('Renamed', 'active'))
        self.assertNotEqual(self.resolver.resolve(candidates, now=10)[self.node('active')], 'Renamed')
        self.assertEqual(self.resolver.resolve(candidates, now=11)[self.node('active')], 'Renamed')
        self.assertEqual(self.resolver.resolve({}, now=12), {})
        self.assertEqual(self.resolver.cache, {})

    def test_database_authorizer_rejects_any_payload_column_read(self):
        connect = sqlite3.connect
        reads = set()
        def restricted(*args, **kwargs):
            db = connect(*args, **kwargs)
            def authorize(action, table, column, database, origin):
                if action == sqlite3.SQLITE_READ and table == 'threads':
                    reads.add(column)
                    if column not in {'id', 'name', 'updated_at', 'archived'}:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            return db
        with patch.object(m.sqlite3, 'connect', side_effect=restricted):
            result = self.resolver.resolve({self.node('active'): 'active'}, now=1)
        self.assertIn(self.node('active'), result)
        self.assertEqual(reads, {'id', 'name', 'updated_at', 'archived'})

    def test_candidate_cache_is_bounded(self):
        self.resolver.resolve({self.node('s'+str(i)): 's'+str(i) for i in range(100)}, now=1)
        self.assertEqual(len(self.resolver.cache),64)

    def test_invalid_schema_and_absent_database_are_label_fallbacks(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('DROP TABLE threads')
        self.assertEqual(self.resolver.resolve({self.node('active'): 'active'}, now=1), {})
        self.database.unlink()
        self.assertEqual(self.resolver.resolve({self.node('active'): 'active'}, now=20), {})
        self.assertFalse(self.database.exists())

    def test_private_snapshot_enrichment_never_enters_state_or_export(self):
        service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
                                  SimpleNamespace(state=lambda: dict(busy=False)), start=False)
        try:
            service._cooler_titles = self.resolver
            service._settings['projects'] = [dict(id='fixture', label='Fixture', root=str(self.root/'project'))]
            event = m.sanitize_hook(dict(hook_event_name='UserPromptSubmit', session_id='active', cwd=str(self.root/'project')))
            service.events.accept(event)
            # Enable the test's explicitly supplied synthetic metadata resolver.
            service.runtime.demo = False
            private = service._collect_cooler_display(service.events.lifecycle_graph(service._settings['projects']),
                                                       service._settings['projects'], {})
            self.assertIn(self.node('active'), private['titles'])
            service._cooler_display = private
            service._send(dict(op='snapshot', snapshot=service.state()))
            self.assertIn('coolerDisplay', service.pending_snapshot['snapshot'])
            self.assertNotIn('coolerDisplay', service.state())
            self.assertNotIn('Active chat', (service.feed/'snapshot.json').read_text())
            self.assertNotIn('title', json.dumps(service.events.lifecycle_graph(service._settings['projects'])))
            event_time = service.events.sessions[('active', 'main')]['observedAt']
            service._collect_cooler_display(service.events.lifecycle_graph(service._settings['projects']), service._settings['projects'], {})
            self.assertEqual(service.events.sessions[('active', 'main')]['observedAt'], event_time)
        finally:
            service.close()

    def test_unregistered_and_stale_chats_are_not_resolved(self):
        service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
                                  SimpleNamespace(state=lambda: dict(busy=False)), start=False)
        try:
            service._cooler_titles = self.resolver
            service.runtime.demo = False
            event = m.sanitize_hook(dict(hook_event_name='UserPromptSubmit', session_id='active', cwd=str(self.root/'other')))
            service.events.accept(event)
            registrations = [dict(id='fixture', root=str(self.root/'project'))]
            value = service._collect_cooler_display(service.events.lifecycle_graph(registrations), registrations, {})
            self.assertEqual(value['titles'], {})
            self.assertTrue(value['activityPartial'])
            event['cwd'] = str(self.root/'project')
            event['id'] = 'stale-event'
            event['observedAt'] = '2026-01-01T00:00:00Z'
            service.events.sessions[('active','main')] = event
            value = service._collect_cooler_display(service.events.lifecycle_graph(registrations), registrations, {})
            self.assertEqual(value['titles'], {})
        finally:
            service.close()

    def test_resource_fields_are_reduced_and_expire_independently(self):
        service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
                                  SimpleNamespace(state=lambda: dict(busy=False)), start=False)
        try:
            fresh = dict(value=55,status='verified',source='nvidia-smi',observedAt=m.utc(),secret='PRIVATE')
            stale = dict(fresh,observedAt='2000-01-01T00:00:00Z')
            service.ai_service = SimpleNamespace(cooler_resources=lambda: [dict(id='msi',gpuId='GPU-fixture',
                cpuTemperature=stale,gpuTemperature=fresh,gpuLoad=dict(fresh,value=0),secret='PRIVATE')])
            value = service._collect_cooler_display({}, [], {})
            host = value['resources'][0]
            self.assertIsNone(host['cpuTemperature']['value'])
            self.assertEqual(host['gpuTemperature']['value'],55)
            self.assertEqual(host['gpuLoad']['value'],0)
            self.assertNotIn('PRIVATE',json.dumps(value))
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main()
