"""Isolated MSI observer tests. No SSH, MSI, installed kit or cache is used."""
from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
import zlib

HERE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location('interlinked_jobs_under_test', HERE / 'interlinked_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
OUTPUT = None
REMOTE = {'__name__': 'isolated_reader_fixture'}
exec(compile(jobs.REMOTE_READER, '<isolated MSI reader>', 'exec'), REMOTE)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.root = OUTPUT / ('case-' + uuid.uuid4().hex)
        self.root.mkdir()
        state = self.root / 'state'
        state.mkdir()
        self.runtime = SimpleNamespace(kit=self.root / 'synthetic-kit', state=state,
                                       demo=False, python=Path(sys.executable))
        jobs.write_json(state / 'OWNER.json', dict(format='interlinked-gui-state-v1',
                                                  kit=str(self.runtime.kit), demo=False))
        self.monitors = []

    def tearDown(self):
        for monitor in self.monitors:
            monitor.close()

    def monitor(self, transport=None, demo=False):
        if demo:
            self.runtime.demo = True
            jobs.write_json(self.runtime.state / 'OWNER.json', dict(format='interlinked-gui-state-v1', kit=str(self.runtime.kit), demo=True))
        value = jobs.JobsMonitor(self.runtime, transport=transport)
        self.monitors.append(value)
        return value

    def wait(self, monitor):
        until = time.monotonic() + 5
        while monitor.view()['refreshing'] and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(monitor.view()['refreshing'], 'Synthetic refresh did not finish')

    def remote_root(self):
        root = self.root / 'synthetic-server-jobs'
        root.mkdir()
        return root

    def remote_job(self, root, number=1, project='synthetic-analysis'):
        jid = format(number, '032x')
        folder = root / jid
        folder.mkdir()
        now = jobs.utc()
        record = dict(format='msi-job-record-v1', jobId=jid, projectId=project,
                      catalogId='msi-project-library-v1', taskName='MSICodingJob-' + jid,
                      taskPath='\\', cacheRoot=r'C:\Users\jlewi\Documents\Codex\MSI-Working-Projects' + '\\' + project,
                      createdUtc=now, spec=dict(secret='must not be returned'))
        status = dict(format='msi-job-status-v1', jobId=jid, projectId=project,
                      status='RUNNING', updatedUtc=now, startedUtc=now,
                      stdout=str(self.root / 'outside-secret.log'))
        jobs.write_json(folder / 'job.json', record)
        jobs.write_json(folder / 'status.json', status)
        (folder / 'stdout.log').write_bytes(b'synthetic stdout\n')
        (folder / 'stderr.log').write_text('', encoding='utf-8')
        return folder, record, status

    def collect(self, root, scheduler=None):
        if scheduler is None:
            scheduler = lambda ids: {jid: ('Running', None) for jid in ids}
        return REMOTE['collect'](root, 'f' * 32, scheduler)

    def test_explicit_refresh_only_and_empty_state_unknown(self):
        calls = []
        monitor = self.monitor(lambda q: calls.append(q))
        view = monitor.view()
        self.assertEqual(calls, [])
        self.assertTrue(view['readOnly'])
        self.assertTrue(view['stale'])
        self.assertEqual(view['connection']['worker'], 'unknown')
        self.assertEqual(view['connection']['ssh'], 'not_checked')

    def test_refresh_receives_reduced_metadata_and_log_on_demand(self):
        monitor = self.monitor(jobs.demo_snapshot)
        monitor.refresh()
        self.wait(monitor)
        view = monitor.view()
        self.assertFalse(view['stale'])
        self.assertEqual(view['connection']['ssh'], 'connected')
        self.assertEqual(view['connection']['worker'], 'unknown')
        self.assertNotIn('dataBase64', json.dumps(view))
        self.assertNotIn('text', view['jobs'][0]['logs']['stdout'])
        self.assertIn('DEMO analysis', monitor.log('a' * 32, 'stdout')['text'])

    def test_duplicate_refresh_refused_without_second_transport(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def transport(q):
            calls.append(q)
            entered.set()
            release.wait(3)
            return jobs.demo_snapshot(q)
        monitor = self.monitor(transport)
        monitor.refresh()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(ValueError):
            monitor.refresh()
        self.assertEqual(len(calls), 1)
        release.set()
        self.wait(monitor)

    def test_failed_refresh_preserves_snapshot_and_hides_arbitrary_error(self):
        monitor = self.monitor(jobs.demo_snapshot)
        monitor.refresh()
        self.wait(monitor)
        path = monitor.root / 'snapshot.json'
        before = path.read_bytes()
        def fail(q):
            raise ValueError('private password or remote command must not enter UI')
        monitor.transport = fail
        monitor.refresh()
        self.wait(monitor)
        self.assertEqual(path.read_bytes(), before)
        view = monitor.view()
        self.assertTrue(view['stale'])
        self.assertEqual(len(view['jobs']), 3)
        self.assertNotIn('password', view['error'])
        self.assertEqual(view['connection']['ssh'], 'unavailable')

    def test_request_identity_mismatch_preserves_previous_observation(self):
        monitor = self.monitor(lambda q: jobs.demo_snapshot('e' * 32))
        monitor.refresh()
        self.wait(monitor)
        self.assertIsNone(monitor.snapshot)
        self.assertIsNotNone(monitor.view()['error'])

    def test_cached_observation_is_stale_after_restart(self):
        monitor = self.monitor(jobs.demo_snapshot)
        monitor.refresh()
        self.wait(monitor)
        restarted = self.monitor(lambda q: self.fail('cache read must not connect'))
        self.assertEqual(len(restarted.view()['jobs']), 3)
        self.assertTrue(restarted.view()['stale'])
        self.assertEqual(restarted.view()['connection']['ssh'], 'not_checked')

    def test_stale_received_and_remote_observation_times(self):
        monitor = self.monitor(jobs.demo_snapshot)
        monitor.refresh()
        self.wait(monitor)
        monitor.clock = lambda: time.time() + jobs.STALE_SECONDS + 1
        self.assertTrue(monitor.view()['stale'])
        monitor.clock = time.time
        monitor.snapshot['observedAt'] = '2000-01-01T00:00:00Z'
        self.assertTrue(monitor.view()['stale'])
        monitor.snapshot['observedAt'] = '2099-01-01T00:00:00Z'
        self.assertTrue(monitor.view()['stale'])

    def test_status_scheduler_disagreement_never_infers_completion(self):
        monitor = self.monitor(jobs.demo_snapshot)
        monitor.refresh()
        self.wait(monitor)
        row = next(r for r in monitor.view()['jobs'] if r['jobId'] == 'c' * 32)
        self.assertEqual(row['status'], 'RUNNING')
        self.assertTrue(row['warning'])
        self.assertTrue(jobs.warning_for(dict(monitor.snapshot['jobs'][1], finishedUtc=None)))
        self.assertTrue(jobs.warning_for(dict(monitor.snapshot['jobs'][0], taskState='Unknown')))

    def test_log_ids_streams_and_unknown_jobs_cannot_supply_paths(self):
        monitor = self.monitor(jobs.demo_snapshot, demo=True)
        for jid, stream in (('../outside', 'stdout'), ('a' * 32, '../../secret'), ('d' * 32, 'stdout')):
            with self.subTest(jid=jid, stream=stream), self.assertRaises(ValueError):
                monitor.log(jid, stream)

    def test_log_controls_removed_but_content_remains_plain_text(self):
        monitor = self.monitor(jobs.demo_snapshot, demo=True)
        data = b'<script>literal</script>\n\x1b[31mANSI\x00'
        monitor.snapshot['jobs'][0]['logs']['stdout'] = dict(available=True, bytes=len(data), truncated=False,
                                                           dataBase64=base64.b64encode(data).decode('ascii'))
        result = monitor.log('a' * 32, 'stdout')
        self.assertIn('<script>literal</script>', result['text'])
        self.assertNotIn('\x1b', result['text'])
        self.assertNotIn('\x00', result['text'])

    def test_snapshot_schema_rejects_injection_duplicates_and_unhashable_fields(self):
        baseline = jobs.demo_snapshot()
        variants = []
        row = copy.deepcopy(baseline); row['jobs'][0]['status'] = []; variants.append(row)
        row = copy.deepcopy(baseline); row['jobs'][0]['taskState'] = {}; variants.append(row)
        row = copy.deepcopy(baseline); row['jobs'][0]['command'] = 'anything'; variants.append(row)
        row = copy.deepcopy(baseline); row['jobs'][1]['jobId'] = row['jobs'][0]['jobId']; variants.append(row)
        row = copy.deepcopy(baseline); row['jobs'][0]['projectId'] = 'CON'; variants.append(row)
        row = copy.deepcopy(baseline); row['sid'] = 'wrong'; variants.append(row)
        for row in variants:
            with self.subTest(row=row), self.assertRaises(ValueError):
                jobs.validate_snapshot(row)

    def test_snapshot_rejects_incomplete_or_oversized_log_tails(self):
        for mutation in (dict(dataBase64='AA=='), dict(bytes=True), dict(truncated=True), dict(dataBase64='A' * 50000)):
            value = jobs.demo_snapshot()
            value['jobs'][0]['logs']['stdout'].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                jobs.validate_snapshot(value)

    def test_foreign_gui_owner_and_unowned_job_directory_preserved(self):
        jobs.write_json(self.runtime.state / 'OWNER.json', dict(format='foreign'))
        self.assertIsInstance(jobs.safe_service(self.runtime), jobs.UnavailableJobs)
        self.assertEqual(jobs.read_json(self.runtime.state / 'OWNER.json'), dict(format='foreign'))

    def test_corrupt_and_deep_cache_cannot_block_core_service(self):
        monitor = self.monitor(jobs.demo_snapshot, demo=True)
        path = monitor.root / 'snapshot.json'
        stored = jobs.read_json(path)
        stored['snapshot']['jobs'][0]['status'] = []
        jobs.write_json(path, stored)
        before = path.read_bytes()
        self.assertIsInstance(jobs.safe_service(self.runtime), jobs.UnavailableJobs)
        self.assertEqual(before, path.read_bytes())
        path.write_text('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}', encoding='utf-8')
        before = path.read_bytes()
        self.assertIsInstance(jobs.safe_service(self.runtime), jobs.UnavailableJobs)
        self.assertEqual(before, path.read_bytes())

    def test_hardlinked_local_snapshot_refused(self):
        monitor = self.monitor(jobs.demo_snapshot, demo=True)
        path = monitor.root / 'snapshot.json'
        os.link(path, self.root / 'linked-observation.json')
        self.assertIsInstance(jobs.safe_service(self.runtime), jobs.UnavailableJobs)

    def test_close_does_not_cancel_transport_or_modify_remote_jobs(self):
        entered = threading.Event()
        release = threading.Event()
        def transport(q):
            entered.set()
            release.wait(3)
            return jobs.demo_snapshot(q)
        monitor = self.monitor(transport)
        monitor.refresh()
        self.assertTrue(entered.wait(1))
        began = time.monotonic()
        monitor.close()
        self.assertLess(time.monotonic() - began, .2)
        self.assertTrue(monitor.view()['refreshing'])
        release.set()
        self.wait(monitor)
        with self.assertRaises(ValueError):
            monitor.refresh()

    def test_source_checkout_cannot_launch_deployed_helper(self):
        monitor = self.monitor()
        with self.assertRaises(ValueError):
            monitor._launch('f' * 32)

    def test_remote_command_is_bounded_and_only_static_reader_runs(self):
        command = jobs.remote_command('f' * 32)
        self.assertLessEqual(len(command), 7500)
        payload = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", command).group(1)
        source = zlib.decompress(base64.b64decode(payload)).decode('utf-8')
        self.assertTrue(source.startswith(jobs.REMOTE_READER))
        self.assertIn('\nverify_identity()\nprint(', source)
        self.assertIn('-I -B -X utf8 -c ', command)
        self.assertNotIn('Start-MSIJob', source)
        self.assertNotIn('Register-ScheduledTask', source)
        self.assertNotIn('Stop-ScheduledTask', source)
        self.assertNotIn('write(', source)
        args = jobs.ssh_arguments('f' * 32, Path(r'C:\synthetic-folder\known_hosts'))
        self.assertLess(len(subprocess.list2cmdline(args)), 32767)
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('HostKeyAlias=MSI', args)
        self.assertIn('NumberOfPasswordPrompts=1', args)
        self.assertNotIn('accept-new', args)
        with self.assertRaises(ValueError):
            jobs.remote_command("';write-anything")

    def test_remote_collect_only_literal_owned_files_not_record_paths(self):
        root = self.remote_root()
        self.remote_job(root)
        (self.root / 'outside-secret.log').write_text('private unrequested file', encoding='utf-8')
        result = self.collect(root)
        jobs.validate_snapshot(result)
        row = result['jobs'][0]
        text = base64.b64decode(row['logs']['stdout']['dataBase64']).decode('utf-8')
        self.assertEqual(text, 'synthetic stdout\n')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('cacheRoot', row)

    def test_remote_collect_skips_foreign_corrupt_or_unhashable_records(self):
        root = self.remote_root()
        self.remote_job(root, 1)
        folder, record, status = self.remote_job(root, 2)
        record['taskName'] = 'another-task'
        jobs.write_json(folder / 'job.json', record)
        folder, record, status = self.remote_job(root, 3)
        status['status'] = []
        jobs.write_json(folder / 'status.json', status)
        folder, record, status = self.remote_job(root, 4)
        status['updatedUtc'] = 'invalid-time'
        jobs.write_json(folder / 'status.json', status)
        result = self.collect(root)
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(result['rejected'], 3)

    def test_remote_log_tail_bounded_with_honest_truncation(self):
        root = self.remote_root()
        folder, _, _ = self.remote_job(root)
        (folder / 'stdout.log').write_bytes(b'PREFIX' + b'x' * (jobs.LOG_BYTES + 12))
        stream = self.collect(root)['jobs'][0]['logs']['stdout']
        self.assertEqual(len(base64.b64decode(stream['dataBase64'])), jobs.LOG_BYTES)
        self.assertTrue(stream['truncated'])
        self.assertEqual(stream['bytes'], jobs.LOG_BYTES + 18)

    def test_remote_hardlinked_log_unavailable_and_metadata_rejected(self):
        root = self.remote_root()
        folder, _, _ = self.remote_job(root, 1)
        os.link(folder / 'stdout.log', self.root / 'linked-log.txt')
        result = self.collect(root)
        self.assertFalse(result['jobs'][0]['logs']['stdout']['available'])
        folder, _, _ = self.remote_job(root, 2)
        os.link(folder / 'status.json', self.root / 'linked-status.json')
        self.assertEqual(self.collect(root)['rejected'], 1)

    def test_remote_list_cap_and_unrelated_names_do_not_fake_truncation(self):
        root = self.remote_root()
        self.remote_job(root)
        for number in range(60):
            (root / ('unrelated-' + str(number))).write_bytes(b'')
        self.assertFalse(self.collect(root)['truncated'])
        for number in range(2, 52):
            self.remote_job(root, number)
        result = self.collect(root)
        self.assertEqual(len(result['jobs']), 50)
        self.assertTrue(result['truncated'])

    def test_missing_scheduler_keeps_receipt_running_and_unknown(self):
        root = self.remote_root()
        self.remote_job(root)
        row = self.collect(root, lambda ids: {})['jobs'][0]
        self.assertEqual(row['status'], 'RUNNING')
        self.assertEqual(row['taskState'], 'Unknown')
        self.assertTrue(jobs.warning_for(row))


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    OUTPUT = Path(args.output).absolute()
    protected = [jobs.KIT, Path(os.environ.get('LOCALAPPDATA', 'C:/not-local-appdata')) / 'ProjectLibrary' / 'Cache']
    for root in protected:
        if OUTPUT == root or root in OUTPUT.parents:
            parser.error('Use a fresh synthetic folder outside the installed kit and production cache.')
    if OUTPUT.exists():
        parser.error('Use a fresh fixture folder; existing evidence is preserved.')
    jobs.ordinary(OUTPUT, missing=True)
    OUTPUT.mkdir(parents=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ObserverTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = dict(format='interlinked-jobs-tests-v1', testsRun=result.testsRun,
                   failures=len(result.failures), errors=len(result.errors), passed=result.wasSuccessful(),
                   syntheticOnly=True, sshInvoked=False, msiContacted=False, installedKitModified=False)
    (OUTPUT / 'results.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
