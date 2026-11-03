"""Synthetic AI context, worker, collection and remote routing tests."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

spec = importlib.util.spec_from_file_location('workspace_tests_subject', Path(__file__).with_name('interlinked_ai_workspaces.py'))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
MODEL = dict(host='THIS-MACHINE', id='writer', digest='a' * 64)
REVIEWER = dict(host='MSI', id='reviewer', digest='b' * 64)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-ai-workspaces-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'example.py').write_text('answer = 1\n', encoding='utf-8')
        (self.source / 'README.md').write_text('A fixture project about owls.\n', encoding='utf-8')
        self.busy = False
        self.lease = 0
        @contextmanager
        def acquire(project):
            self.assertEqual(project, 'fixture')
            if self.lease:
                raise ValueError('Held fixture lease')
            self.lease += 1
            try:
                yield
            finally:
                self.lease -= 1
        self.engine = w.Workspaces(self.root / 'state', registered_projects=lambda: [dict(id='fixture', label='Fixture', root=str(self.source))], busy=lambda _: self.busy, acquire=acquire)
        self.engine.approve('fixture')

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def body(self, **kwargs):
        return dict(projectId='fixture', prompt='Change answer to 2.', model=MODEL, permissions=['read', 'write'], requestId=uuid.uuid4().hex, **kwargs)

    def wait(self, row):
        self.engine.threads[row['id']].join(5)
        result = self.engine.task(row['id'])
        self.assertNotIn(result['status'], w.ACTIVE, result)
        return result

    def infer(self, model, messages, tools, cancel):
        if model['digest'] == REVIEWER['digest']:
            return dict(content='{"approved":true,"findings":[]}')
        if messages[-1]['role'] == 'tool':
            return dict(content='Changed answer.')
        return dict(content='', tool_calls=[dict(function=dict(name='write_file', arguments=dict(path='example.py', content='answer = 2\n')))])

    def test_index_exclusions_citations_and_stale(self):
        (self.source / '.env').write_text('SECRET=owls')
        (self.source / 'secrets.json').write_text('{"owl":"secret"}')
        (self.source / 'binary.txt').write_bytes(b'owls\x00binary')
        (self.source / 'settings.json').write_text('{"api_key":"an_actual_secret_value", "topic":"owls"}')
        (self.source / 'node_modules').mkdir()
        (self.source / 'node_modules' / 'fake.md').write_text('owls')
        indexed = self.engine.index('fixture')
        self.assertEqual(indexed['files'], 2)
        rows = self.engine.search('owls')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['path'], 'README.md')
        self.assertFalse(rows[0]['stale'])
        (self.source / 'README.md').write_text('changed')
        self.assertTrue(self.engine.search('owls')[0]['stale'])
        self.engine.approve('fixture', False)
        self.assertEqual(self.engine.search('owls'), [])

    def test_changed_registration_loses_approval(self):
        other = self.root / 'other'
        other.mkdir()
        self.engine.registered_projects = lambda: [dict(id='fixture', root=str(other))]
        with self.assertRaisesRegex(ValueError, 'Approve'):
            self.engine.index('fixture')

    def test_paths_and_proprietary_files_rejected(self):
        for path in ('../outside.py', 'C:/file.py', '/file.py', 'a\\b.py', 'nul.py', '.git/config', 'file.MVP', 'file.MV_', '.env', 'config:secret.txt', 'a/../b.py', 'a./b.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                w.relative(path)

    def test_hardlinks_excluded(self):
        outside = self.root / 'outside.md'
        outside.write_text('private owl')
        try:
            os.link(outside, self.source / 'linked.md')
        except OSError:
            self.skipTest('Fixture hardlink unavailable')
        self.assertNotIn('linked.md', w.scan(self.source)[0])

    def test_worker_copy_review_and_apply(self):
        row = self.wait(self.engine.start(self.body(), self.infer, REVIEWER))
        self.assertEqual(row['status'], 'READY_FOR_REVIEW')
        self.assertEqual(row['review']['status'], 'APPROVED')
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')
        self.assertEqual(row['result']['changes'][0]['path'], 'example.py')
        applied = self.engine.apply(row['id'], row['result']['sha256'])
        self.assertEqual(applied['status'], 'APPLIED')
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')
        self.assertEqual((self.root / 'state/tasks' / row['id'] / 'ApplyBackup/example.py').read_text(), 'answer = 1\n')
        self.assertEqual(self.lease, 0)

    def test_conflict_blocks_all_collection(self):
        row = self.wait(self.engine.start(self.body(), self.infer, REVIEWER))
        (self.source / 'README.md').write_text('User edit')
        with self.assertRaisesRegex(ValueError, 'Project changed'):
            self.engine.apply(row['id'], row['result']['sha256'])
        self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')

    def test_active_session_blocks_collection_and_start(self):
        row = self.wait(self.engine.start(self.body(), self.infer, REVIEWER))
        self.busy = True
        with self.assertRaisesRegex(ValueError, 'active project'):
            self.engine.apply(row['id'], row['result']['sha256'])
        with self.assertRaisesRegex(ValueError, 'active project'):
            self.engine.start(self.body(), self.infer)

    def test_duplicate_request_is_not_replayed(self):
        body = self.body()
        row = self.wait(self.engine.start(body, self.infer))
        other = self.engine.start(body, self.infer)
        self.assertEqual(row['id'], other['id'])
        with self.assertRaisesRegex(ValueError, 'different task'):
            self.engine.start({**body, 'prompt': 'Different task'}, self.infer)

    def test_same_digest_is_not_independent_review(self):
        row = self.wait(self.engine.start(self.body(), self.infer, {**MODEL, 'host': 'MSI'}))
        self.assertEqual(row['status'], 'REVIEW_PENDING')
        self.assertEqual(row['review']['status'], 'PENDING')

    def test_readonly_worker_cannot_edit(self):
        body = self.body()
        body['permissions'] = ['read']
        row = self.wait(self.engine.start(body, self.infer))
        self.assertEqual(row['result']['changes'], [])
        self.assertEqual(row['actions'][0]['status'], 'FAILED')

    def test_cancel_retains_partial_results(self):
        entered = threading.Event()
        def infer(model, messages, tools, cancel):
            entered.set()
            cancel.wait(4)
            raise InterruptedError('Canceled fixture inference')
        row = self.engine.start(self.body(), infer)
        self.assertTrue(entered.wait(3))
        self.engine.cancel(row['id'])
        row = self.wait(row)
        self.assertEqual(row['status'], 'CANCELED')
        self.assertIsNotNone(row['result'])

    def test_cancelled_transport_valueerror_is_canceled(self):
        entered = threading.Event()
        def infer(model, messages, tools, cancel):
            entered.set()
            cancel.wait(4)
            raise ValueError('Transport canceled')
        row = self.engine.start(self.body(), infer)
        self.assertTrue(entered.wait(3))
        self.engine.cancel(row['id'])
        self.assertEqual(self.wait(row)['status'], 'CANCELED')

    def test_correction_does_not_expand_step_budget(self):
        def infer(model, messages, tools, cancel):
            if model['digest'] == REVIEWER['digest']:
                return dict(content='{"approved":false,"findings":["Still needs a fix"]}')
            return self.infer(model, messages, tools, cancel)
        row = self.wait(self.engine.start(self.body(maxSteps=2), infer, REVIEWER))
        self.assertEqual(row['stepsUsed'], 2)
        self.assertNotIn('correctionCycle', row)
        self.assertEqual(row['review']['status'], 'FINDINGS')

    def test_unreviewed_check_refused(self):
        with self.assertRaisesRegex(ValueError, 'reviewed check'):
            self.engine.start(self.body(checkProfiles=['arbitrary']), self.infer)

    def test_one_correction_cycle(self):
        reviews = []
        def infer(model, messages, tools, cancel):
            if model['digest'] == REVIEWER['digest']:
                reviews.append(True)
                return dict(content='{"approved":false,"findings":["Fixture finding"]}')
            return self.infer(model, messages, tools, cancel)
        row = self.wait(self.engine.start(self.body(), infer, REVIEWER))
        self.assertEqual(len(reviews), 2)
        self.assertEqual(row['correctionCycle'], 1)
        self.assertEqual(row['review']['status'], 'FINDINGS')

    def test_malformed_review_stays_pending(self):
        def infer(model, messages, tools, cancel):
            return dict(content='Everything is fine') if model['digest'] == REVIEWER['digest'] else self.infer(model, messages, tools, cancel)
        row = self.wait(self.engine.start(self.body(), infer, REVIEWER))
        self.assertEqual(row['status'], 'REVIEW_PENDING')
        self.assertIsNotNone(row['result'])

    def test_source_roundtrip_bom_preserves_original_baseline(self):
        (self.source / 'example.py').write_bytes(b'\xef\xbb\xbfanswer = 1\n')
        bundle = self.engine.export_source('fixture')
        remote = w.Workspaces(self.root / 'remote')
        try:
            prepared = remote.import_source(bundle)
            row = remote.start({**self.body(), 'projectId': prepared['projectId']}, self.infer)
            remote.threads[row['id']].join(5)
            result = remote.export_result(row['id'])
            self.assertEqual(result['baseline'], bundle['baseline'])
            imported = self.engine.import_result('fixture', result)
            self.engine.apply(imported['id'], result['sha256'])
            self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')
        finally:
            remote.close()

    def test_import_refuses_tamper_and_traversal(self):
        bundle = self.engine.export_source('fixture')
        bundle['files'][0]['content'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            self.engine.import_source(bundle)
        bundle = self.engine.export_source('fixture')
        bundle['files'][0]['path'] = '../outside.py'
        with self.assertRaises(ValueError):
            self.engine.import_source(bundle)

    def test_recovery_does_not_replay_actions(self):
        row = self.wait(self.engine.start(self.body(), self.infer))
        self.engine._update(row['id'], status='RUNNING')
        recovered = w.Workspaces(self.root / 'state')
        try:
            self.assertEqual(recovered.task(row['id'])['status'], 'RECOVERY_REQUIRED')
            self.assertEqual(recovered.threads, {})
        finally:
            recovered.close()

    def test_apply_rejects_changed_result_and_missing_lease(self):
        row = self.wait(self.engine.start(self.body(), self.infer))
        with self.assertRaisesRegex(ValueError, 'Result changed'):
            self.engine.apply(row['id'], 'f' * 64)
        self.engine.acquire = None
        with self.assertRaisesRegex(ValueError, 'workflow lease'):
            self.engine.apply(row['id'], row['result']['sha256'])

    def test_remote_execution_and_local_inference(self):
        remote = w.Workspaces(self.root / 'remote')
        pending, answers = {}, {}
        seen_hosts = []
        def bridge(model, messages, tools, cancel):
            ident = uuid.uuid4().hex
            pending[ident] = dict(id=ident, model=model, messages=messages, tools=tools)
            end = time.monotonic() + 4
            while ident not in answers and time.monotonic() < end:
                if cancel.wait(.01):
                    raise InterruptedError('Remote canceled')
            pending.pop(ident, None)
            response = answers.pop(ident)
            if 'error' in response:
                raise ValueError(response['error'])
            return response['response']
        class Node:
            worker_operation_recovery = True
            def execute(self, action, payload, cancel=None, **kwargs):
                if action == 'worker_prepare':
                    return remote.import_source(payload)
                if action == 'worker_start':
                    return remote.start(payload, bridge)
                if action == 'worker_status':
                    return dict(task=remote.task(payload['taskId']), pendingInference=list(pending.values()))
                if action == 'worker_infer_result':
                    answers[payload['id']] = payload
                    return dict(accepted=True)
                if action == 'worker_result':
                    return remote.export_result(payload['taskId'])
                if action == 'worker_cancel':
                    return remote.cancel(payload['taskId'])
                raise ValueError(action)
        def infer(model, messages, tools, cancel):
            seen_hosts.append(model['host'])
            return self.infer(model, messages, tools, cancel)
        try:
            row = self.wait(self.engine.start_remote(self.body(), Node(), infer, REVIEWER))
            self.assertEqual(row['status'], 'READY_FOR_REVIEW', row)
            self.assertIn('THIS-MACHINE', seen_hosts)
            self.assertIn('MSI', seen_hosts)
            self.assertEqual((self.source / 'example.py').read_text(), 'answer = 1\n')
            self.engine.apply(row['id'], row['result']['sha256'])
            self.assertEqual((self.source / 'example.py').read_text(), 'answer = 2\n')
        finally:
            remote.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows native containment required')
    def test_native_reviewed_check(self):
        self.engine.register_check('fixture', 'fixture', [sys.executable, '-I', '-B', '-c', 'print("contained-check")'], 5)
        body = self.body(checkProfiles=['fixture'])
        body['permissions'] = ['read', 'check']
        def infer(model, messages, tools, cancel):
            if messages[-1]['role'] == 'tool':
                return dict(content='Check complete.')
            return dict(content='', tool_calls=[dict(function=dict(name='run_check', arguments=dict(profile='fixture')))])
        row = self.wait(self.engine.start(body, infer))
        self.assertEqual(len(row['checks']), 1, row)
        self.assertEqual(row['checks'][0]['exitCode'], 0)
        self.assertIn('contained-check', row['checks'][0]['stdout'])

    @unittest.skipUnless(os.name == 'nt', 'Windows native containment required')
    def test_native_timeout_terminates_descendants(self):
        command = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);print(p.pid,flush=True);time.sleep(60)'
        self.engine.register_check('fixture', 'timeout', [sys.executable, '-I', '-B', '-c', command], 1)
        body = self.body(checkProfiles=['timeout'])
        body['permissions'] = ['read', 'check']
        def infer(model, messages, tools, cancel):
            if messages[-1]['role'] == 'tool':
                return dict(content='Reported timeout.')
            return dict(content='', tool_calls=[dict(function=dict(name='run_check', arguments=dict(profile='timeout')))])
        row = self.wait(self.engine.start(body, infer))
        self.assertEqual(row['actions'][0]['status'], 'FAILED')
        self.assertIn('budget expired', row['actions'][0]['error'])
        output = self.root / 'state/tasks' / row['id'] / 'logs' / (row['actions'][0]['id'] + '.stdout')
        child_pid = int(output.read_text().strip())
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel.OpenProcess(0x1000, False, child_pid)
        if process:
            try:
                code = wintypes.DWORD()
                self.assertTrue(kernel.GetExitCodeProcess(process, ctypes.byref(code)))
                self.assertNotEqual(code.value, 259)
            finally:
                kernel.CloseHandle(process)

    def test_remote_profiles_are_host_local(self):
        bundle = self.engine.export_source('fixture')
        remote = w.Workspaces(self.root / 'remote')
        try:
            prepared = remote.import_source(bundle)
            with self.assertRaisesRegex(ValueError, 'reviewed check'):
                remote.start({**self.body(checkProfiles=['fixture']), 'projectId': prepared['projectId']}, self.infer)
            if os.name == 'nt':
                remote.register_check(prepared['projectId'], 'fixture', [sys.executable, '-I', '-B', '-c', 'print(1)'])
                prepared2 = remote.import_source(bundle)
                self.assertIn('fixture', remote.state['profiles'][prepared2['projectId']])
        finally:
            remote.close()


if __name__ == '__main__':
    unittest.main()
