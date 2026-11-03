"""Native same-user IPC acceptance; isolated fixtures and no model calls."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_ai_ipc as ipc


@unittest.skipUnless(os.name == 'nt', 'Windows named pipes required')
class PipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'bridge' / 'endpoint.json'
        self.calls = []
        def dispatch(action, body):
            self.calls.append((action, body))
            if action == 'reject': raise ValueError('private token and path must not escape')
            if action == 'slow': time.sleep(.3)
            return dict(action=action, value=body)
        self.server = ipc.PipeServer(self.path, dispatch).start()

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def change_endpoint(self, mutate):
        value = json.loads(self.path.read_text())
        mutate(value)
        self.path.write_text(json.dumps(value))
        return value

    def test_roundtrip_reconnect_and_private_endpoint(self):
        endpoint = ipc.read_endpoint(self.path)
        self.assertEqual(endpoint['identity'], ipc.process_identity())
        self.assertEqual(ipc.request(self.path, 'read', {'text':'Unicode Ω'}), {'action':'read','value':{'text':'Unicode Ω'}})
        self.assertEqual(ipc.request(self.path, 'next', {}), {'action':'next','value':{}})
        self.assertEqual(len(self.calls), 2)

    def test_second_coordinator_refused(self):
        with self.assertRaisesRegex(ipc.IPCError, 'coordinator_already_running'):
            ipc.PipeServer(self.path, lambda *_: None).start()
        self.assertEqual(ipc.request(self.path,'read',{})['action'], 'read')

    def test_process_identity_change_refused_before_dispatch(self):
        self.change_endpoint(lambda v: v['identity'].update(created=1))
        with self.assertRaisesRegex(ipc.IPCError,'coordinator_changed'):
            ipc.request(self.path,'read',{})
        self.assertEqual(self.calls, [])

    def test_wrong_session_refused_before_dispatch(self):
        self.change_endpoint(lambda v: v.update(session='0'*64))
        with self.assertRaises((OSError, ipc.IPCError)):
            ipc.request(self.path,'read',{})
        self.assertEqual(self.calls, [])

    def test_broadened_endpoint_acl_refused(self):
        ipc.protect_file(self.path, 'WD')
        with self.assertRaisesRegex(ipc.IPCError, 'invalid_endpoint_acl'):
            ipc.request(self.path,'read',{})
        self.assertEqual(self.calls, [])
        ipc.protect_file(self.path, ipc.process_identity()['sid'])

    def test_dispatch_error_does_not_leak(self):
        with self.assertRaisesRegex(ipc.IPCError, '^invalid_request$'):
            ipc.request(self.path, 'reject', {})
        self.assertEqual(ipc.request(self.path,'read',{})['action'], 'read')

    def test_client_timeout_then_recovery(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ipc.IPCError, 'transport_timeout'):
            ipc.request(self.path,'slow',{},timeout=.08)
        self.assertLess(time.monotonic() - start, 1)
        time.sleep(.35)
        self.assertEqual(ipc.request(self.path,'read',{})['action'], 'read')

    def test_clean_stop_removes_only_own_endpoint(self):
        self.server.close()
        self.assertFalse(self.path.exists())
        with self.assertRaises(ipc.IPCError): ipc.request(self.path,'read',{},timeout=.1)

    def test_endpoint_extra_keys_refused(self):
        self.change_endpoint(lambda v: v.update(guiToken='not-real'))
        with self.assertRaisesRegex(ipc.IPCError, 'invalid_endpoint'):
            ipc.request(self.path,'read',{})
        self.assertEqual(self.calls, [])

    def test_linked_endpoint_refused(self):
        alias = self.path.with_name('alias.json')
        os.link(self.path, alias)
        with self.assertRaisesRegex(ipc.IPCError,'invalid_endpoint'):
            ipc.request(self.path,'read',{})
        alias.unlink()


class FramingTests(unittest.TestCase):
    def test_duplicate_keys_nonfinite_and_oversize_refused(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b' '*(ipc.MAX_MESSAGE+1), b'['*10000+b']'*10000):
            with self.assertRaises((ipc.IPCError,ValueError)): ipc.decode(raw)
        with self.assertRaises(ipc.IPCError): ipc.encode({'x':'a'*ipc.MAX_MESSAGE})


if __name__ == '__main__': unittest.main()
