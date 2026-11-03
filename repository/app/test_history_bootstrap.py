"""Real isolated-Python bootstrap checks with private synthetic state only.

Run normally or pass --python PATH to exercise another reviewed Python runtime.
Each child starts with -I -B and loads only its entry module by exact file path.
No archive, installed state, model inference or network listener is used.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PYTHON = Path(sys.executable)
APP = Path(__file__).resolve().parent

CHILD = r'''
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

body = json.loads(sys.stdin.buffer.read())
app, profile = Path(body['app']).resolve(), Path(body['profile']).resolve()
assert sys.flags.isolated and sys.flags.dont_write_bytecode
assert all(Path(p).resolve() != app for p in sys.path if p)
assert Path(os.environ['LOCALAPPDATA']).resolve() == profile
assert not any(name.startswith('interlinked_history_') for name in sys.modules)

def prohibit_external_activity(event, args):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen'}:
        raise AssertionError('Bootstrap attempted external activity: ' + event)
sys.addaudithook(prohibit_external_activity)

def entry(name):
    spec = importlib.util.spec_from_file_location('isolated_' + name, app / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

if body['mode'] == 'node':
    module = entry('interlinked_ai_node')
    # The authoritative identity is a fixture; even a test running on a machine
    # named MSI must never start the actual hardware telemetry sampler.
    module.socket.gethostname = lambda: 'ISOLATED-BOOTSTRAP-FIXTURE'
    runtime = module.LocalNode(profile/'fixture-node', identity={'computer':'MSI','sid':'S-1-5-21-1-2-3-1001'})
    runtime._request = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Unexpected runtime request'))
    try:
        state = runtime.execute('history', {'action':'state','body':{}})
        assert state['role'] == 'primary' and state['available'] is False
        assert state['generation'] is None and state['preparedGenerations'] == []
        service = runtime._history_service
        assert runtime.execute('history', {'action':'state','body':{}})['generation'] is None
        assert runtime._history_service is service
        assert (profile/'INTERLINKED'/'History'/'archive'/'OWNER.json').exists()
    finally:
        runtime.close()
else:
    module = entry('interlinked_ai')
    class FixtureNode:
        def __init__(self, root): pass
        def inventory(self, **kwargs): raise AssertionError('Unexpected inventory request')
        def chat(self, *args, **kwargs): raise AssertionError('Unexpected inference request')
        def close(self): pass
    state_root = profile/'fixture-app'; state_root.mkdir()
    runtime = module.AIService(SimpleNamespace(state=state_root,kit=profile,demo=True), node_factory=FixtureNode)
    try:
        service = runtime.local_history()
        state = service.state()
        assert state['available'] is True and state['ready'] is False
        assert state['role'] == 'replica' and state['generation'] is None
        assert runtime.local_history() is service
        assert (state_root/'history-test'/'replica'/'OWNER.json').exists()
        assert service.gateway is None
        if body['mode'] == 'gateway':
            gateway = sys.modules['interlinked_history_gateway'].HistoryGateway(service, profile/'fixture-clients', port=0)
            try:
                status = gateway.status()
                assert status['running'] is False and status['clients'] == []
                assert gateway.server is None
            finally:
                gateway.close()
    finally:
        runtime.close()

names = ('interlinked_history_runtime', 'interlinked_history_store', 'interlinked_history_import',
         'interlinked_history_gateway', 'interlinked_history_service', 'interlinked_configuration')
for name in names:
    assert Path(sys.modules[name].__file__).resolve().parent == app
assert all(Path(p).resolve() != app for p in sys.path if p)
print(json.dumps({'status':'PASS','mode':body['mode'],'isolated':True,'loadedSiblings':len(names),
                  'python':list(sys.version_info[:2]),'externalActivity':False}))
'''


class IsolatedBootstrapTests(unittest.TestCase):
    def run_child(self, mode):
        with tempfile.TemporaryDirectory(prefix='interlinked-bootstrap-fixture-') as temporary:
            root = Path(temporary)
            profile, cwd = root/'profile', root/'untrusted-cwd'
            profile.mkdir(); cwd.mkdir()
            for name in ('interlinked_history_service', 'interlinked_history_runtime', 'interlinked_configuration'):
                (cwd/(name+'.py')).write_text("raise AssertionError('Untrusted working-directory module was imported')\n", encoding='utf-8')
            env = dict(os.environ, LOCALAPPDATA=str(profile))
            value = {'app':str(APP), 'profile':str(profile), 'mode':mode}
            result = subprocess.run([str(PYTHON), '-I', '-B', '-c', CHILD],
                input=json.dumps(value).encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=cwd, env=env, timeout=30, creationflags=0x08000000 if os.name=='nt' else 0)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertEqual(result.stderr, b'')
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual(report['mode'], mode)
            self.assertTrue(report['isolated'])
            self.assertFalse(report['externalActivity'])
            self.assertNotIn(str(APP), result.stdout.decode())
            self.assertNotIn(str(profile), result.stdout.decode())

    def test_authoritative_node_history_bootstraps_from_exact_entry_path(self):
        self.run_child('node')

    def test_demo_coordinator_history_bootstraps_without_app_on_sys_path(self):
        self.run_child('ai')

    def test_gateway_private_state_bootstraps_without_listener_or_keys(self):
        self.run_child('gateway')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--python')
    args, remaining = parser.parse_known_args()
    if args.python: PYTHON = Path(args.python).resolve()
    unittest.main(argv=[sys.argv[0], *remaining])
