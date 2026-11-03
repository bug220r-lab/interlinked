"""Isolated Git collector tests; no repository writes, hardware or MSI access."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('master_git_fixture', Path(__file__).with_name('interlinked_master.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class GitCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-git-fixture-')
        self.root = Path(self.temp.name)
        self.service = m.MasterService(SimpleNamespace(state=self.root/'state', kit=self.root/'kit', demo=True),
            SimpleNamespace(state=lambda:dict(busy=False)), start=False)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def projects(self, count):
        rows = []
        for index in range(count):
            root = self.root/('project-' + str(index))
            root.mkdir(exist_ok=True)
            rows.append(dict(id='fixture-' + str(index), label='Fixture ' + str(index), root=str(root)))
        return rows

    def test_slow_git_does_not_delay_lifecycle_publication_and_pool_is_bounded(self):
        projects = self.projects(12)
        self.service._settings['projects'] = projects
        self.service.runtime.demo = False
        entered, release = threading.Event(), threading.Event()
        gate = threading.Lock()
        counts = dict(active=0, maximum=0, calls=0)
        def slow(root, stop):
            with gate:
                counts['calls'] += 1
                counts['active'] += 1
                counts['maximum'] = max(counts['maximum'], counts['active'])
                if counts['active'] == 2:entered.set()
            release.wait(3)
            with gate:counts['active'] -= 1
            return dict(status='available', branch='main', head='a'*40, dirty=False, observedAt=m.utc())
        with patch.object(m, 'git_observation', side_effect=slow):
            try:
                self.service.refresh()
                self.assertTrue(entered.wait(1))
                self.service.events.accept(m.sanitize_hook(dict(hook_event_name='UserPromptSubmit',
                    session_id='fixture-session', cwd=projects[0]['root'], observedAt=m.utc())))
                started = time.monotonic()
                self.service.refresh()
                self.assertLess(time.monotonic()-started, .8, 'Git must not hold up fresh lifecycle snapshots')
                state = self.service.state()
                self.assertEqual(state['projects'][0]['activity']['status'], 'working')
                self.assertLess(time.time()-m.timestamp(state['utc']), 2)
                for tick in range(101, 140):
                    self.service._git_collector.schedule(projects, now=tick)
                self.assertEqual(counts['calls'], 2)
                self.assertLessEqual(len(self.service._git_collector.pending), 10)
                self.assertEqual(counts['maximum'], 2)
            finally:
                self.service.stop.set();release.set()
                self.service._git_collector.close()

    def test_root_replacement_drops_old_in_flight_result(self):
        old, new = self.projects(2)
        new['id'] = old['id']
        entered, release = threading.Event(), threading.Event()
        collector = self.service._git_collector
        def observe(root, stop):
            if root == old['root']:
                entered.set();release.wait(3)
            return dict(status='available', branch=root, head='a'*40, dirty=False, observedAt=m.utc())
        with patch.object(m, 'git_observation', side_effect=observe):
            try:
                collector.schedule([old], now=100)
                self.assertTrue(entered.wait(1))
                collector.schedule([new], now=101)
                with collector.wake:
                    self.assertTrue(collector.wake.wait_for(lambda:(new['id'],new['root']) in collector.observations, timeout=1))
                release.set()
                with collector.wake:
                    self.assertTrue(collector.wake.wait_for(lambda:not collector.active, timeout=1))
                self.assertEqual(collector.snapshot([new])[new['id']]['branch'], new['root'])
                self.assertNotIn((old['id'],old['root']), collector.observations)
            finally:
                release.set();collector.close()

    def test_each_root_retains_its_own_timestamp_and_unavailable_result(self):
        rows = self.projects(2)
        collector = self.service._git_collector
        timestamps = [m.utc(), (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()]
        def observe(root, stop):
            index = next(i for i,row in enumerate(rows) if row['root'] == root)
            return dict(status='available' if index == 0 else 'unavailable', branch=None, head=None,
                dirty=None, observedAt=timestamps[index])
        with patch.object(m, 'git_observation', side_effect=observe):
            collector.schedule(rows)
            with collector.wake:
                self.assertTrue(collector.wake.wait_for(lambda:len(collector.observations) == 2, timeout=1))
        values = collector.snapshot(rows)
        self.assertEqual(values[rows[0]['id']]['observedAt'], timestamps[0])
        self.assertEqual(values[rows[1]['id']]['observedAt'], timestamps[1])
        self.assertEqual(values[rows[1]['id']]['status'], 'unavailable')

    def test_stale_future_or_missing_git_cannot_establish_current_result(self):
        observed = datetime.now(timezone.utc)
        receipt = dict(format='interlinked-master-result-v1', kind='test', result='passed',
            observedAt=observed.isoformat(), baseline=dict(head='a'*40,clean=True))
        git = dict(status='available', head='a'*40, dirty=False)
        for stamp in (None, (observed-timedelta(seconds=16)).isoformat(), (observed+timedelta(seconds=2)).isoformat()):
            with self.subTest(observedAt=stamp):
                health = m.receipt_health(receipt, dict(git, observedAt=stamp))
                self.assertEqual(health['status'], 'unknown')
                self.assertFalse(health['current'])
        self.assertTrue(m.receipt_health(receipt, dict(git, observedAt=m.utc()))['current'])

    def test_refresh_labels_old_git_stale_instead_of_renewing_its_time(self):
        project = self.projects(1)[0]
        self.service._settings['projects'] = [project]
        stamp = (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
        self.service._git_collector.observations[(project['id'],project['root'])] = dict(
            status='available', head='a'*40, dirty=False, branch='main', observedAt=stamp)
        self.service.refresh(collect_git=False)
        git = self.service.state()['projects'][0]['git']
        self.assertEqual(git['status'], 'stale')
        self.assertEqual(git['observedAt'], stamp)

    def test_shutdown_cancels_active_checks_and_discards_pending_work(self):
        collector = self.service._git_collector
        entered = threading.Event()
        calls = []
        def observe(root, stop):
            calls.append(root);entered.set()
            stop.wait(2)
            return dict(status='unavailable', observedAt=m.utc())
        with patch.object(m, 'git_observation', side_effect=observe):
            collector.schedule(self.projects(12))
            self.assertTrue(entered.wait(1))
            collector.close()
        self.assertTrue(all(not thread.is_alive() for thread in collector.threads))
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(list(collector.pending), [])
        self.assertEqual(collector.observations, {})

    def test_cancelled_and_timed_out_owned_git_are_killed(self):
        project = self.projects(1)[0]
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                stop = threading.Event()
                clock = [0.0]
                class Process:
                    returncode = None
                    killed = False
                    def poll(self):return self.returncode
                    def kill(self):self.killed=True;self.returncode=-1
                    def communicate(self, timeout):
                        if self.killed:return b'', None
                        if cancel:stop.set()
                        else:clock[0]=4.0
                        raise subprocess.TimeoutExpired('synthetic git', timeout)
                process = Process()
                with patch.object(m.subprocess, 'Popen', return_value=process), patch.object(m.time, 'monotonic', side_effect=lambda:clock[0]):
                    result = m.git_observation(project['root'], stop)
                self.assertTrue(process.killed)
                self.assertEqual(result['status'], 'unavailable')

    def test_fresh_git_parser_retains_read_only_command_guards(self):
        project = self.projects(1)[0]
        output = ('# branch.oid ' + 'a'*40 + '\n# branch.head main\n').encode()
        process = SimpleNamespace(returncode=0, poll=lambda:0, communicate=lambda timeout:(output,None))
        with patch.object(m.subprocess, 'Popen', return_value=process) as launch:
            value = m.git_observation(project['root'])
        args = launch.call_args.args[0]
        self.assertIn('--no-optional-locks', args)
        self.assertIn('core.fsmonitor=false', args)
        self.assertIn('core.untrackedCache=false', args)
        self.assertEqual(value['head'], 'a'*40)
        self.assertFalse(value['dirty'])


if __name__ == '__main__':unittest.main(verbosity=2)
