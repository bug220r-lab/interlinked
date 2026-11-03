"""Sealed passive Git dependency tests; fake binaries are never executed."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import interlinked_history_runtime as runtime


class GitDependency(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='history-git-seal-')
        self.root = Path(self.temp.name)
        self.base = self.root / 'INTERLINKED/HistoryRuntime/git-2.56.0.4'
        self.exe = self.base / 'cmd/git.exe'
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'synthetic nonexecutable fixture')
        self.value = dict(format='interlinked-history-git-v1', platform='windows-x64', executable='cmd/git.exe',
                          files=[dict(path='cmd/git.exe', bytes=self.exe.stat().st_size,
                                      sha256=hashlib.sha256(self.exe.read_bytes()).hexdigest())])
        self.environment = mock.patch.dict(os.environ, LOCALAPPDATA=str(self.root))
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def seal(self):
        raw = json.dumps(self.value).encode()
        (self.base / 'GIT-CHECKSUMS.json').write_bytes(raw)
        return mock.patch.object(runtime, 'GIT_MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())

    def test_exact_dependency_resolves_without_execution(self):
        with self.seal(), mock.patch('subprocess.run', side_effect=AssertionError('Must not execute')):
            self.assertEqual(runtime.load_git(), str(self.exe))

    def test_modified_manifest_refused(self):
        with self.seal():
            (self.base / 'GIT-CHECKSUMS.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'manifest differs'):
                runtime.load_git()

    def test_same_size_payload_tampering_refused(self):
        with self.seal():
            self.exe.write_bytes(b'x' * self.exe.stat().st_size)
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                runtime.load_git()

    def test_extra_or_missing_files_refused(self):
        with self.seal():
            (self.base / 'unexpected.dll').write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                runtime.load_git()
        (self.base / 'unexpected.dll').unlink()
        with self.seal():
            self.exe.unlink()
            with self.assertRaises((ValueError, OSError)):
                runtime.load_git()

    def test_unsafe_and_duplicate_manifest_paths_refused(self):
        original = dict(self.value['files'][0])
        for name in ('../outside.exe', 'C:/outside.exe'):
            self.value['files'] = [dict(original, path=name)]
            with self.seal(), self.assertRaisesRegex(ValueError, 'path'):
                runtime.load_git()
        self.value['files'] = [original, dict(original)]
        with self.seal(), self.assertRaisesRegex(ValueError, 'path'):
            runtime.load_git()

    def test_installed_missing_dependency_never_uses_arbitrary_path(self):
        with (mock.patch.object(runtime, '__file__', str(self.root / 'installed/interlinked_history_runtime.py')),
              mock.patch('shutil.which', side_effect=AssertionError('Installed PATH fallback forbidden'))):
            self.assertIsNone(runtime.load_git())

    def test_bundle_uses_sealed_ucrt_helper_path_and_clears_inherited_git_options(self):
        import interlinked_history_import as importer
        import subprocess
        helpers = self.base / 'ucrt64/libexec/git-core'
        helpers.mkdir(parents=True)
        with (self.seal(), mock.patch.dict(os.environ, GIT_EXEC_PATH='foreign', GIT_CONFIG_COUNT='1', GIT_DIR='foreign'),
              mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')) as run):
            self.assertEqual(list(importer._git_bundle(self.root / 'fixture.bundle', {}, None)), [])
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][0], str(self.exe))
                env = call.kwargs['env']
                self.assertEqual(env['GIT_EXEC_PATH'], str(helpers))
                self.assertNotIn('GIT_DIR', env)
                self.assertNotIn('GIT_CONFIG_COUNT', env)
                self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
                self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
                self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
                self.assertEqual(env['GIT_NO_REPLACE_OBJECTS'], '1')


if __name__ == '__main__':
    unittest.main()
