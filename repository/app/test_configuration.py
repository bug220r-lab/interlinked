"""Synthetic local configuration durability, revision and recovery fixtures."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('configuration_under_test', HERE/'interlinked_configuration.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='interlinked-configuration-')
        self.root = Path(self.temporary.name)
        self.owner = dict(format='fixture-owner-v1', identity='synthetic')
        (self.root/'OWNER.json').write_text(json.dumps(self.owner), encoding='utf-8')
        self.store = self.make_store()

    def tearDown(self):
        self.temporary.cleanup()

    def owner_check(self):
        owner = json.loads((self.root/'OWNER.json').read_text(encoding='utf-8'))
        if owner != self.owner:
            raise ValueError('Foreign fixture owner')
        return owner

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {'items'} or not isinstance(value['items'], list) or not all(type(item) is int for item in value['items']):
            raise ValueError('Invalid fixture value')
        return deepcopy(value)

    def legacy(self, value):
        if not isinstance(value, dict) or value.get('format') != 'fixture-v1':
            raise ValueError('Unknown fixture legacy format')
        return dict(items=value['items'])

    def make_store(self):
        return c.ConfigStore(self.root/'settings.json', 'master', self.owner_check, self.validate, self.legacy, dict(items=[]))

    def test_legacy_read_is_unchanged_and_migrates_on_change(self):
        path = self.root/'settings.json'
        raw = b'{"format":"fixture-v1","items":[1]}\n'
        path.write_bytes(raw)
        state = self.store.status()
        self.assertTrue(state['revision'].startswith('legacy:'))
        self.assertEqual(path.read_bytes(), raw)
        self.store.save(dict(items=[1]), expected_revision=state['revision'])
        self.assertEqual(path.read_bytes(), raw)
        changed = self.store.save(dict(items=[1, 2]), expected_revision=state['revision'])
        self.assertRegex(changed['revision'], r'^[a-f0-9]{32}$')
        self.assertEqual(self.make_store().status()['revision'], changed['revision'])
        self.assertEqual(len(self.store.history()['items']), 1)

    def test_stale_save_conflicts_without_history_or_write(self):
        base = self.store.status()
        changed = self.store.save(dict(items=[1]), expected_revision=base['revision'])
        before = (self.root/'settings.json').read_bytes()
        with self.assertRaises(c.Conflict) as raised:
            self.store.save(dict(items=[2]), expected_revision=base['revision'])
        self.assertEqual(raised.exception.current['revision'], changed['revision'])
        self.assertEqual((self.root/'settings.json').read_bytes(), before)
        self.assertEqual(self.store.history()['items'], [])

    def test_three_previous_versions_and_noop(self):
        for item in range(6):
            self.store.save(dict(items=[item]), reason='Fixture change')
        rows = self.store.history()['items']
        self.assertEqual(len(rows), 3)
        current = self.store.status()
        self.store.save(current['value'], expected_revision=current['revision'])
        self.assertEqual(self.store.history()['items'], rows)
        values = [self.store.preview(row['id'])['proposed']['items'][0] for row in rows]
        self.assertEqual(values, [4, 3, 2])

    def test_failed_commit_preserves_primary_and_does_not_rotate(self):
        self.store.save(dict(items=[1]))
        before = self.store.status()
        original = c.atomic
        def failed(path, data):
            if path == self.store.path:
                raise OSError('Synthetic write failure')
            return original(path, data)
        with patch.object(c, 'atomic', failed):
            with self.assertRaises(OSError):
                self.store.save(dict(items=[2]))
        self.assertEqual(self.store.status(), before)
        self.assertEqual(self.store.history()['items'], [])

    def test_preview_is_bound_to_live_bytes_and_source(self):
        self.store.save(dict(items=[1]))
        self.store.save(dict(items=[2]))
        row = self.store.history()['items'][0]
        preview = self.store.preview(row['id'])
        self.store.save(dict(items=[3]))
        with self.assertRaises(c.Conflict):
            self.store.restore(preview['previewId'])
        preview = self.store.preview(row['id'])
        path = self.store.history_root/(row['id']+'.json')
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError):
            self.store.restore(preview['previewId'])

    def test_corrupt_primary_preserved_and_restore_generates_new_revision(self):
        first = self.store.save(dict(items=[1]))
        self.store.save(dict(items=[2]))
        row = self.store.history()['items'][0]
        raw = b'{corrupt fixture bytes\x00'
        (self.root/'settings.json').write_bytes(raw)
        current = self.store.status()
        self.assertFalse(current['available'])
        with self.assertRaises(ValueError):
            self.store.save(dict(items=[]))
        preview = self.store.preview(row['id'], lambda value, status: dict(items=value['items']+[9]))
        restored = self.store.restore(preview['previewId'])
        self.assertEqual(restored['value'], dict(items=[1, 9]))
        self.assertNotEqual(restored['revision'], first['revision'])
        preserved = list(self.store.history_root.glob('corrupt-*.bin'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), raw)

    def test_foreign_and_corrupt_history_are_not_pruned(self):
        self.store.save(dict(items=[1]))
        self.store.save(dict(items=[2]))
        row = self.store.history()['items'][0]
        path = self.store.history_root/(row['id']+'.json')
        original = path.read_bytes()
        value = json.loads(original)
        value['sha256'] = '0'*64
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertFalse(self.store.history()['available'])
        before = (self.root/'settings.json').read_bytes()
        with self.assertRaises(ValueError):
            self.store.save(dict(items=[3]))
        self.assertEqual((self.root/'settings.json').read_bytes(), before)
        self.assertTrue(path.exists())
        path.write_bytes(original)
        owner = self.store.history_root/'OWNER.json'
        marker = json.loads(owner.read_bytes())
        marker['namespace'] = 'library'
        owner.write_text(json.dumps(marker), encoding='utf-8')
        with self.assertRaises(c.UnsafeConfiguration):
            self.store.history()

    def test_hardlinked_primary_refused_and_owner_failure_not_empty(self):
        self.store.save(dict(items=[1]))
        os.link(self.root/'settings.json', self.root/'second.json')
        with self.assertRaises(c.UnsafeConfiguration):
            self.store.status()
        (self.root/'second.json').unlink()
        (self.root/'OWNER.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.status()

    def test_oversized_corruption_is_unavailable_and_preserved_before_restore(self):
        self.store.save(dict(items=[1]))
        self.store.save(dict(items=[2]))
        row = self.store.history()['items'][0]
        raw = b'x'*(c.LIMIT+1)
        (self.root/'settings.json').write_bytes(raw)
        self.assertFalse(self.store.status()['available'])
        preview = self.store.preview(row['id'])
        self.store.restore(preview['previewId'])
        self.assertEqual(next(self.store.history_root.glob('corrupt-*.bin')).read_bytes(), raw)
        with (self.root/'settings.json').open('wb') as stream:
            stream.truncate(c.RECOVERY_LIMIT+1)
        self.assertFalse(self.store.status()['available'])
        with self.assertRaises(ValueError):
            self.store.preview(row['id'])

    def test_opened_lock_identity_and_attributes_are_rechecked(self):
        self.store.save(dict(items=[1]))
        original = os.fstat
        for changed in ('identity', 'reparse', 'offline', 'recall'):
            def replaced(fd):
                result = original(fd)
                fields = {name: getattr(result, name) for name in dir(result) if name.startswith('st_')}
                if changed == 'identity':fields['st_ino'] += 1
                else:fields['st_file_attributes'] = {'reparse':0x400, 'offline':0x1000, 'recall':0x400000}[changed]
                return SimpleNamespace(**fields)
            with self.subTest(changed=changed), patch.object(c.os, 'fstat', replaced):
                with self.assertRaises(c.UnsafeConfiguration):
                    self.store.status()

    def test_foreign_primary_namespace_is_an_identity_refusal(self):
        self.store.save(dict(items=[1]))
        record = json.loads((self.root/'settings.json').read_bytes())
        record['namespace'] = 'library'
        (self.root/'settings.json').write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaises(c.UnsafeConfiguration):
            self.store.status()

    def test_atomic_server_deltas_from_two_instances(self):
        stores = [self.store, self.make_store()]
        gate = threading.Barrier(2)
        errors = []
        def change(index):
            try:
                gate.wait()
                stores[index].update(lambda value: dict(items=value['items']+[index]))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=change, args=(index,)) for index in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.store.status()['value']['items']), [0, 1])


if __name__ == '__main__':
    unittest.main()
