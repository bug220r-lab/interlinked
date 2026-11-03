"""Synthetic history/coordinator integration; no personal data or live models."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interlinked_ai as ai
import interlinked_ai_bridge as bridge
import interlinked_configuration as config
from interlinked_history_service import HistoryController, HistoryNode, PROFILE, relative
from interlinked_history_store import HistoryStore


def record(text='PRIVATE_HISTORY_CANARY', **extra):
    value = dict(id=hashlib.sha256(b'fixture').hexdigest(), revision=hashlib.sha256(text.encode()).hexdigest(),
                 kind='conversation', title='Fixture history', text=text, timestamp=None, project='', memory_allowed=True,
                 metadata={'conversationId': 'fixture-chat', 'role': 'user', 'onCurrentBranch': True})
    value.update(extra)
    return value


class Node:
    def __init__(self, root): self.calls=[]
    def inventory(self, **kw):
        return dict(models=[dict(name='fixture', digest='a'*64, size=1, capabilities=['completion'])], loaded=[], resources={}, runtime={'version':'fixture-v1'},inferencePolicySha256=None)
    def chat(self, model, messages, **kw):
        self.calls.append((messages, kw))
        prompt=messages[-1]['content']
        content='{"pending":7}' if '"pending":7' in prompt else '{"latest_failed":2}' if '"latest_failed":2' in prompt else '{"owner":null}' if '"owner":null' in prompt else 'Fixture answer [1].'
        return {'message':{'content':content}}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/'app').mkdir()
        self.ai=ai.AIService(SimpleNamespace(state=self.root/'app',kit=self.root,demo=True),node_factory=Node)
        self.history=self.ai.local_history()
        primary=HistoryStore(self.root/'primary')
        self.record=record(); generation=primary.build([self.record]); primary.activate(generation['generationId'])
        manifest=primary.snapshot_manifest()
        self.history.store.install_snapshot(manifest, lambda path,offset,length:primary.snapshot_read_chunk(manifest['snapshotId'],path,offset,length))
        self.history._embedder=lambda:None
        self.ai.refresh()
    def tearDown(self): self.ai.close(); self.temp.cleanup()
    def calibrate(self):
        result=self.history.calibrate({'host':'local','model':'fixture'},threading.Event())
        self.assertTrue(result['passed'])
    def wait_chat(self,row):
        deadline=time.monotonic()+5
        while self.ai.conversations[row['id']]['status'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.01)
        return self.ai.conversations[row['id']]
    def test_profile_is_separate_and_context_is_budgeted(self):
        before=self.ai.settings_value()
        self.calibrate()
        row=self.wait_chat(self.ai.chat({'text':'PRIVATE_HISTORY_CANARY','historyEnabled':True,'host':'local','model':'fixture'}))
        self.assertEqual(row['status'],'completed',row.get('error'))
        sent,kw=self.ai.nodes['local'].calls[-1]
        self.assertIn('PRIVATE_HISTORY_CANARY',sent[0]['content'])
        self.assertEqual(kw['options'],{'num_ctx':16384,'num_predict':4096})
        self.assertEqual(self.ai.settings_value(),before)
    def test_scope_change_does_not_replay_old_answer(self):
        self.calibrate()
        row=self.wait_chat(self.ai.chat({'text':'PRIVATE_HISTORY_CANARY','historyEnabled':True,'model':'fixture'}))
        row['messages'][-1]['content']='PRIVATE_DERIVED_ANSWER'
        row=self.wait_chat(self.ai.chat({'text':'A fresh question','historyEnabled':True,'historyProject':'other','conversationId':row['id']}))
        sent=self.ai.nodes['local'].calls[-1][0]
        self.assertNotIn('PRIVATE_DERIVED_ANSWER',json.dumps(sent))
    def test_handoff_has_exact_note_and_sources_and_no_model_call(self):
        before=len(self.ai.nodes['local'].calls)
        result=self.history.post('handoff',{'sourceIds':[self.record['id']],'text':'Selected note'})
        self.assertIn('Selected note',result['text']);self.assertIn('PRIVATE_HISTORY_CANARY',result['text'])
        self.assertEqual(len(self.ai.nodes['local'].calls),before)
    def test_exclusion_removes_source_and_handoff(self):
        self.history.post('exclude',{'id':self.record['id'],'enabled':True})
        self.assertIsNone(self.history.store.source(self.record['id']))
        with self.assertRaises(ValueError):self.history.post('handoff',{'sourceIds':[self.record['id']]})
    def test_status_does_not_expose_source_text(self):
        self.assertNotIn('PRIVATE_HISTORY_CANARY',json.dumps(self.history.store.status()))
    def test_reserved_and_escaping_paths_refused(self):
        for p in ('../x','/x','a\\b','NUL','x/COM1.txt','a:stream'):
            with self.subTest(p=p),self.assertRaises(ValueError):relative(p)
    def test_prepared_exclusion_immediately_applies_to_related_active_replica(self):
        primary=HistoryStore(self.root/'primary')
        self.addCleanup(primary.close)
        prepared=record(text='New prepared source', id=hashlib.sha256(b'new-message').hexdigest())
        generation=primary.build([prepared])['generationId']
        def peer(action, body=None, cancel=None):
            if action=='exclusion-binding': return primary.exclusion_binding(body['id'],body.get('generation'))
            if action=='source': return {'source':primary.source(body['id'],generation_id=body.get('generation'))}
            if action=='exclude': return primary.exclude(body['id'],body.get('enabled',True),generation_id=body.get('generation'))
            if action=='state': return dict(primary.status(),exclusions=primary.exclusions())
            raise AssertionError(action)
        self.history._peer=peer
        self.history.post('exclude',{'id':prepared['id'],'generation':generation,'enabled':True})
        self.assertIsNone(self.history.store.source(self.record['id']))
        self.assertTrue(self.history.state()['exclusions'])
        self.history.post('exclude',{'id':prepared['id'],'generation':generation,'enabled':False})
        self.assertIsNotNone(self.history.store.source(self.record['id']))
    def test_unapproved_profile_cannot_answer(self):
        with self.assertRaisesRegex(ValueError,'Calibrate'):self.history.answer([{'role':'user','content':'hello'}])
    def test_changed_runtime_revokes_profile(self):
        self.calibrate()
        self.assertFalse(self.history.profile_matches({'host':'local','digest':'a'*64},{'runtime':{'version':'changed'}}))

    def test_exclusion_during_inference_discards_answer_and_retained_context(self):
        self.calibrate()
        entered, release = threading.Event(), threading.Event()
        runtime = self.ai.nodes['local']; original = runtime.chat
        def delayed(model, messages, **kwargs):
            runtime.calls.append((messages, kwargs)); entered.set()
            if not release.wait(5): raise AssertionError('Fixture answer was never released.')
            return {'message': {'content': 'OBSOLETE_PRIVATE_ANSWER_7f1c'}}
        runtime.chat = delayed
        row = self.ai.chat({'text':'Find Fixture history', 'historyEnabled':True, 'model':'fixture'})
        worker = self.ai.threads[-1]
        try:
            self.assertTrue(entered.wait(3), 'Inference did not receive its prepared context.')
            self.assertIn('PRIVATE_HISTORY_CANARY', json.dumps(runtime.calls[-1][0]))
            self.history.post('exclude', {'id':self.record['id'], 'enabled':True})
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        result = self.wait_chat(row)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('discarded', result['error'])
        self.assertFalse(any(message['role']=='assistant' for message in result['messages']))
        persisted = (self.ai.root/'conversations'/(row['id']+'.json')).read_text()
        self.assertNotIn('OBSOLETE_PRIVATE_ANSWER_7f1c', persisted)
        self.assertEqual(json.loads(persisted)['status'], 'failed')
        runtime.chat = original
        result = self.wait_chat(self.ai.chat({'text':'What remains?', 'historyEnabled':True, 'model':'fixture', 'conversationId':row['id']}))
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertNotIn('PRIVATE_HISTORY_CANARY', json.dumps(runtime.calls[-1][0]))
        self.assertNotIn('Find Fixture history', json.dumps(runtime.calls[-1][0]))

    def test_equal_count_memory_edit_during_inference_discards_answer(self):
        primary = HistoryStore(self.root/'primary'); self.addCleanup(primary.close)
        memory = primary.propose_memory('MEMORY_BEFORE_EDIT_c42b', [self.record['id']])
        primary.review_memory(memory['id'], 'approve')
        def replicate():
            manifest = primary.snapshot_manifest()
            self.history.store.install_snapshot(manifest, lambda path, offset, length: primary.snapshot_read_chunk(manifest['snapshotId'], path, offset, length))
        replicate(); self.calibrate()
        before = self.history.store.status()
        entered, release = threading.Event(), threading.Event()
        runtime = self.ai.nodes['local']
        def delayed(model, messages, **kwargs):
            runtime.calls.append((messages, kwargs)); entered.set()
            if not release.wait(5): raise AssertionError('Fixture answer was never released.')
            return {'message': {'content': 'OBSOLETE_MEMORY_ANSWER_bae6'}}
        runtime.chat = delayed
        row = self.ai.chat({'text':'Find Fixture history', 'historyEnabled':True, 'model':'fixture'})
        worker = self.ai.threads[-1]
        try:
            self.assertTrue(entered.wait(3))
            self.assertIn('MEMORY_BEFORE_EDIT_c42b', json.dumps(runtime.calls[-1][0]))
            primary.review_memory(memory['id'], 'edit', text='MEMORY_AFTER_EDIT_8a21')
            primary.review_memory(memory['id'], 'approve')
            replicate()
            after = self.history.store.status()
            self.assertEqual(before['memories'], after['memories'], 'Counts alone cannot detect this policy change.')
            self.assertNotEqual(before['policyRevision'], after['policyRevision'])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        result = self.wait_chat(row)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('discarded', result['error'])
        self.assertNotIn('OBSOLETE_MEMORY_ANSWER_bae6', json.dumps(result))

    def prepared_fixture(self):
        primary = HistoryNode(self.root/'first-primary', self.ai.nodes['local'])
        client = HistoryController(self.ai, root=self.root/'first-client')
        self.addCleanup(primary.close); self.addCleanup(primary.store.close); self.addCleanup(client.close)
        prepared = record('FIRST_PREPARED_CANARY_d24f')
        generation = primary.store.build([prepared])['generationId']
        def peer(action, body=None, cancel=None):
            return primary.execute(action, body or {}, cancel,caller_identity='fixture-pair')
        client._peer = peer
        return primary, client, prepared, generation

    def test_prepared_exclusion_and_restore_before_first_activation(self):
        primary, client, prepared, generation = self.prepared_fixture()
        self.assertFalse(client.state()['ready'])
        self.assertIsNone(primary.store.status()['generation'])
        body = {'id':prepared['id'], 'generation':generation, 'enabled':True}
        client.post('exclude', body)
        client.sync()
        state = client.state()
        self.assertFalse(state['ready'])
        self.assertTrue(any(row.get('sourceId')==prepared['id'] and row['enabled'] for row in state['exclusions']))
        self.assertIsNone(primary.store.source(prepared['id'], generation_id=generation))
        self.assertEqual(client.pending_exclusions, {})
        client.post('exclude', dict(body, enabled=False))
        client.sync()
        self.assertIsNotNone(primary.store.source(prepared['id'], generation_id=generation))
        self.assertFalse(any(row['enabled'] for row in client.state()['exclusions']))
        self.assertFalse(client.state()['ready'])
        self.assertIsNone(primary.store.status()['generation'])

    def test_unknown_prepared_source_cannot_poison_pending_sync(self):
        _, client, _, generation = self.prepared_fixture()
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                with self.assertRaises(ValueError):
                    client.post('exclude', {'id':'f'*64, 'generation':generation, 'enabled':enabled})
                self.assertEqual(client.pending_exclusions, {})
        client.sync()
        self.assertFalse(client.state()['ready'])

    def test_long_path_upload_preserves_bytes_and_resumes_without_rewriting_conflicts(self):
        root = self.root/'long-upload'
        primary = HistoryNode(root, self.ai.nodes['local'])
        transfer_id = hashlib.sha256(b'long-path-transfer-fixture').hexdigest()
        name = 'assets/payload-' + 'x'*200 + '.dat'
        payload = bytes(range(256))*3 + 'Unicode fixture 😀'.encode('utf-8')
        entry = dict(path=name, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        manifest = {'id':transfer_id, 'files':[entry]}
        target = root/'transfers'/transfer_id/'source'/Path(name)
        self.assertGreater(len(str(target.absolute())), 260, 'Fixture must exercise a real long destination path.')
        def upload(offset, data):
            return primary.execute('upload-chunk', dict(id=transfer_id, path=name, offset=offset,
                data=base64.b64encode(data).decode('ascii')))
        def contents():
            with open(config.fs(target), 'rb') as stream: return stream.read()
        try:
            primary.execute('upload-begin', manifest)
            self.assertEqual(upload(0, payload[:128])['bytes'], 128)
            self.assertEqual(upload(0, payload[:128])['bytes'], 128)
            self.assertEqual(contents(), payload[:128])
            with self.assertRaisesRegex(ValueError, 'preceding'):
                upload(300, payload[300:350])
            self.assertEqual(contents(), payload[:128])

            # A new owner resumes the persisted receipt and partial file.
            primary.close(); primary.store.close()
            primary = HistoryNode(root, self.ai.nodes['local'])
            primary.execute('upload-begin', manifest)
            self.assertEqual(upload(64, payload[64:256])['bytes'], 256)
            self.assertEqual(contents(), payload[:256])
            with self.assertRaisesRegex(ValueError, 'Repeated transfer chunk differs'):
                upload(80, b'!' + payload[81:120])
            self.assertEqual(contents(), payload[:256])
            upload(256, payload[256:])
            self.assertEqual(contents(), payload)
            self.assertEqual(hashlib.sha256(contents()).hexdigest(), entry['sha256'])
            self.assertEqual(upload(0, payload)['bytes'], len(payload))
            self.assertEqual(contents(), payload)
            with self.assertRaisesRegex(ValueError, 'different files'):
                primary.execute('upload-begin', dict(manifest, files=[dict(entry, sha256='f'*64)]))
            self.assertEqual(contents(), payload)
        finally:
            primary.close(); primary.store.close()
            # Remove only the fixture file using its long-path spelling; the
            # normal TemporaryDirectory cleanup owns the remaining short dirs.
            self.assertTrue(target.absolute().is_relative_to(self.root.absolute()))
            if os.path.exists(config.fs(target)): os.unlink(config.fs(target))

    def test_production_startup_requests_history_without_blocking_ui_or_double_initializing(self):
        entered, release, constructed = threading.Event(), threading.Event(), threading.Event()
        calls, service, errors, requested = [], [], [], []
        backend = SimpleNamespace(close=lambda: None)
        original_companion = ai.companion
        def history_factory(owner):
            calls.append(owner); entered.set()
            if not release.wait(5): raise AssertionError('Fixture initialization was never released.')
            return backend
        helper = SimpleNamespace(load_service=lambda: SimpleNamespace(HistoryController=history_factory))
        def companion(name):
            return helper if name=='interlinked_history_runtime' else original_companion(name)
        app = self.root/'startup-app'; app.mkdir()
        def construct():
            try:
                service.append(ai.AIService(SimpleNamespace(state=app,kit=self.root,demo=False),node_factory=Node))
                constructed.set()
            except BaseException as exc:
                errors.append(exc); constructed.set()
        creator = threading.Thread(target=construct)
        requester = None
        with mock.patch.object(ai, 'companion', side_effect=companion):
            creator.start()
            try:
                self.assertTrue(entered.wait(3), 'Startup must request history before opening the Assistant tab.')
                self.assertTrue(constructed.wait(1), 'Background history setup must not block service construction.')
                self.assertEqual(errors, [])
                self.assertIsNone(service[0].history_backend)
                acquired = service[0].lock.acquire(timeout=.5)
                self.assertTrue(acquired, 'History setup must not occupy the main AI state lock.')
                if acquired: service[0].lock.release()
                requester = threading.Thread(target=lambda: requested.append(service[0].local_history()))
                requester.start()
            finally:
                release.set(); creator.join(5)
                if requester: requester.join(5)
                for owner in service:
                    for thread in owner.threads:
                        if thread is not threading.current_thread() and thread.name.endswith('(start_history)'): thread.join(5)
                    owner.close()
            self.assertEqual(len(calls), 1)
            self.assertEqual(requested, [backend])
            self.assertIs(service[0].history_backend, backend)

    def test_demo_startup_does_not_initialize_history_until_requested(self):
        entered = threading.Event()
        original_companion = ai.companion
        backend = SimpleNamespace(close=lambda: None)
        def history_factory(owner): entered.set(); return backend
        helper = SimpleNamespace(load_service=lambda: SimpleNamespace(HistoryController=history_factory))
        app = self.root/'demo-startup-app'; app.mkdir()
        with mock.patch.object(ai, 'companion', side_effect=lambda name: helper if name=='interlinked_history_runtime' else original_companion(name)):
            service = ai.AIService(SimpleNamespace(state=app,kit=self.root,demo=True),node_factory=Node)
            try:
                self.assertFalse(entered.wait(.1))
                self.assertIsNone(service.history_backend)
                self.assertIs(service.local_history(), backend)
                self.assertTrue(entered.is_set())
            finally:
                service.close()

    def test_restored_gateway_port_conflict_preserves_searchable_replica_and_keys(self):
        import interlinked_history_gateway as gateway_module
        root = self.root/'port-conflict-history'
        gateway = gateway_module.HistoryGateway(self.history, root/'clients', port=0)
        try:
            credential = gateway.create('Retained fixture client', historyEnabled=True, project='Fixture')
        finally:
            gateway.close()
        registry = (root/'clients'/'clients.json').read_bytes()
        self.assertNotIn(credential['key'].encode(), registry)
        primary = HistoryStore(self.root/'primary'); self.addCleanup(primary.close)
        replica = HistoryStore(root/'replica', role='replica'); self.addCleanup(replica.close)
        manifest = primary.snapshot_manifest()
        replica.install_snapshot(manifest, lambda path, offset, length: primary.snapshot_read_chunk(manifest['snapshotId'], path, offset, length))
        original_gateway = gateway_module.HistoryGateway
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            if os.name=='nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(('127.0.0.1', 0)); occupied.listen(1)
            port = occupied.getsockname()[1]
            owner = SimpleNamespace(runtime=SimpleNamespace(demo=False), nodes={})
            with mock.patch.object(gateway_module, 'HistoryGateway', side_effect=lambda controller, folder: original_gateway(controller, folder, port=port)):
                controller = HistoryController(owner, root=root)
                try:
                    state = controller.state()
                    self.assertTrue(state['available'] and state['ready'])
                    self.assertFalse(state['gateway']['running'])
                    self.assertIn('port', state['gateway']['error'].lower())
                    self.assertIn(str(port), state['gateway']['error'])
                    self.assertIsNotNone(controller.store.source(self.record['id']))
                    self.assertTrue(controller.store.search('PRIVATE_HISTORY_CANARY'))
                    self.assertEqual((root/'clients'/'clients.json').read_bytes(), registry)
                finally:
                    controller.close()


class FastEvent:
    def __init__(self): self.event = threading.Event()
    def is_set(self): return self.event.is_set()
    def set(self): self.event.set()
    def clear(self): self.event.clear()
    def wait(self, seconds): return self.event.is_set()


class MemoryBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='history-memory-batches-')
        self.root = Path(self.temp.name); (self.root / 'app').mkdir()
        self.ai = ai.AIService(SimpleNamespace(state=self.root/'app', kit=self.root, demo=True), node_factory=Node)
        self.history = self.ai.local_history()
        self.primary = HistoryNode(self.root / 'primary-node', self.ai.nodes['local'])
        self.rows = [record('Synthetic durable preference %02d' % index,
            id=hashlib.sha256(('source-%02d' % index).encode()).hexdigest(),
            metadata={'conversationId': 'source-%02d' % index, 'role':'user', 'onCurrentBranch': True}) for index in range(25)]
        self.generation = self.primary.store.build(self.rows)['generationId']; self.primary.store.activate(self.generation)
        self.history._peer = self.peer
        self.history.calibrate({'host':'local', 'model':'fixture'}, threading.Event())
        self.inferences = 0
        def infer(*args, **kwargs):
            self.inferences += 1
            return {'content':json.dumps({'text':'PRIVATE_DURABLE_CANDIDATE'})}
        self.ai.infer = infer
        self.progress = []
    def tearDown(self):
        self.ai.close(); self.primary.close(); self.primary.store.close(); self.temp.cleanup()
    def peer(self, action, body=None, cancel=None):
        return self.primary.execute(action, body or {}, cancel,caller_identity='fixture-pair')
    def generate(self, event=None):
        return self.history.generate_memories({}, event or FastEvent(), self.progress.append)
    def restart(self):
        folder = self.history.root; self.history.close()
        self.history = HistoryController(self.ai, root=folder)
        self.history._peer = self.peer
        self.ai.history_backend = self.history
    def current_run(self):
        queue = json.loads(next((self.history.root/'memory-queues').glob('*.json')).read_text())
        return json.loads((self.history.root/'memory-runs'/(queue['currentRunId']+'.json')).read_text())

    def test_completed_batches_advance_beyond_twenty_then_stop(self):
        first = self.generate(); second = self.generate(); final = self.generate()
        self.assertEqual((first['processed'], second['processed'], final['processed']), (20, 5, 0))
        self.assertTrue(second['exhausted'] and final['exhausted'])
        self.assertEqual(self.inferences, 25)
        self.assertEqual(len(self.primary.store.memories()), 25)
        self.assertNotIn('PRIVATE_DURABLE_CANDIDATE', json.dumps(self.progress))
        self.assertNotIn('PRIVATE_DURABLE_CANDIDATE', (self.history.root/'operations'/'jobs.json').read_text())

    def test_crash_after_primary_rpc_reuses_candidate_and_preserves_approval(self):
        receipts, fail = [], [True]
        def uncertain(action, body=None, cancel=None):
            result = self.peer(action, body, cancel)
            if action == 'memory-propose' and fail[0]:
                fail[0] = False; receipts.append(result['id'])
                raise ConnectionError('PRIVATE_UNKNOWN_RPC_OUTCOME')
            return result
        self.history._peer = uncertain
        with self.assertRaises(ConnectionError): self.generate()
        self.assertEqual(self.inferences, 1)
        run = self.current_run()
        self.assertEqual(run['status'], 'failed')
        self.assertNotIn('PRIVATE_DURABLE_CANDIDATE', json.dumps(run))
        self.primary.store.review_memory(receipts[0], 'approve')
        self.restart()
        result = self.generate()
        self.assertTrue(result['resumed'])
        self.assertEqual(self.inferences, 20, 'The already generated candidate must not be inferred twice.')
        memories = self.primary.store.memories()
        self.assertEqual(len(memories), 20)
        self.assertEqual(next(row for row in memories if row['id']==receipts[0])['status'], 'approved')

    def test_cancel_after_saved_candidate_resumes_without_regeneration(self):
        cancel = FastEvent()
        def infer(*args, **kwargs):
            self.inferences += 1; cancel.set()
            return {'content':'{"text":"PRIVATE_DURABLE_CANDIDATE"}'}
        self.ai.infer = infer
        with self.assertRaises(InterruptedError): self.generate(cancel)
        self.assertEqual(self.primary.store.memories(), [])
        self.assertEqual(len(list((self.history.root/'memory-drafts').glob('*.json'))), 1)
        self.assertEqual(self.current_run()['status'], 'interrupted')
        def resume_infer(*args, **kwargs):
            self.inferences += 1
            return {'content':'{"text":"PRIVATE_DURABLE_CANDIDATE"}'}
        self.ai.infer = resume_infer
        self.restart()
        result = self.generate()
        self.assertTrue(result['resumed'])
        self.assertEqual(self.inferences, 20)
        self.assertEqual(len(self.primary.store.memories()), 20)

    def test_excluded_pending_source_is_skipped_on_resume(self):
        fail = [True]
        def uncertain(action, body=None, cancel=None):
            result = self.peer(action, body, cancel)
            if action=='memory-propose' and fail[0]:
                fail[0]=False; raise ConnectionError('Synthetic receipt interruption')
            return result
        self.history._peer = uncertain
        with self.assertRaises(ConnectionError): self.generate()
        pending = self.current_run()['sources'][1]['id']
        self.primary.store.exclude(pending)
        self.restart()
        result = self.generate()
        self.assertEqual(result['newRunReason'], 'source_selection_changed')
        self.assertEqual(result['blocked'], 0)
        self.assertEqual(self.inferences, 20)
        self.assertEqual(len(self.primary.store.memories()), 20)
        self.assertFalse(any(ref['id']==pending for m in self.primary.store.memories() for ref in m['sources']))

    def test_local_pending_exclusion_blocks_memory_before_primary_sync(self):
        source = min(self.rows, key=lambda row:row['id'])
        self.history.store.exclude_record(self.primary.store.source(source['id']))
        self.assertIsNotNone(self.primary.store.source(source['id']))
        result = self.generate()
        self.assertEqual(result['blocked'], 1)
        self.assertEqual(self.inferences, 19)
        self.assertEqual(len(self.primary.store.memories()), 19)
        self.assertIsNotNone(self.primary.store.source(source['id']))

    def test_generation_drift_refuses_old_run_then_starts_new_with_fixed_payload(self):
        changed = [False]
        def infer(*args, **kwargs):
            self.inferences += 1
            if not changed[0]:
                changed[0] = True
                generation = self.primary.store.build(self.rows)['generationId']
                self.primary.store.activate(generation)
            return {'content':'{"text":"PRIVATE_DURABLE_CANDIDATE"}'}
        self.ai.infer = infer
        from interlinked_history_service import MemoryRunDrift
        with self.assertRaises(MemoryRunDrift): self.generate()
        old = self.current_run()
        self.assertEqual(old['status'], 'drift')
        self.assertEqual(self.primary.store.memories(), [])
        result = self.generate()
        self.assertEqual(result['newRunReason'], 'source_or_model_binding_changed')
        self.assertNotEqual(result['runId'], old['id'])
        self.assertEqual(self.inferences, 20)
        self.assertEqual(json.loads((self.history.root/'memory-runs'/(old['id']+'.json')).read_text())['status'], 'drift')

    def test_new_generation_reuses_prior_reviewed_proposals(self):
        self.generate()
        first = self.primary.store.memories()[0]
        self.primary.store.review_memory(first['id'], 'reject')
        generation = self.primary.store.build(self.rows)['generationId']; self.primary.store.activate(generation)
        self.generate()
        self.assertEqual(self.inferences, 20)
        self.assertEqual(len(self.primary.store.memories()), 20)
        self.assertEqual(next(row for row in self.primary.store.memories() if row['id']==first['id'])['status'], 'rejected')

    def test_primary_wire_memory_passage_is_bounded_and_restrictions_remain(self):
        long_source = record('x'*100000, id='e'*64, metadata={'conversationId':'long', 'role':'user'})
        restricted = record('Never memorize', id='f'*64, memory_allowed=True,
                            metadata={'conversationId':'restricted', 'doNotRemember':True})
        group = record('Unknown group participant', id='d'*64, kind='group', memory_allowed=True,
                       metadata={'conversationId':'group', 'role':'user', 'speaker':'unspecified'})
        generation = self.primary.store.build([long_source, restricted, group])['generationId']; self.primary.store.activate(generation)
        value = self.peer('memory-source', {'id':long_source['id']})
        self.assertLessEqual(len(value['source']['text'].encode('utf-8')), 6000)
        self.assertTrue(value['source']['memoryExcerptTruncated'])
        self.assertEqual(value['source']['revision'], long_source['revision'])
        self.assertIsNone(self.peer('memory-source', {'id':restricted['id']})['source'])
        self.assertIsNone(self.peer('memory-source', {'id':group['id']})['source'])
        with self.assertRaises(ValueError): self.peer('memory-inputs', {'limit':21})

    def test_empty_filtered_page_advances_cursor_without_inference(self):
        cursor = {'generationId':self.generation, 'afterId':self.rows[0]['id']}
        calls = []
        def pages(action, body=None, cancel=None):
            if action=='memory-inputs':
                calls.append(body.get('after'))
                if len(calls)==1:
                    return dict(sources=[], nextCursor=cursor, generationId=self.generation, exhausted=False, scanned=512,
                                selectionRevision=self.primary.store.memory_selection_revision())
                from interlinked_history_service import memory_source
                return dict(sources=[memory_source(self.primary.store.source(self.rows[1]['id']))],
                            nextCursor=None, generationId=self.generation, exhausted=True, scanned=1,
                            selectionRevision=self.primary.store.memory_selection_revision())
            return self.peer(action, body, cancel)
        self.history._peer = pages
        first = self.generate()
        self.assertEqual(first['processed'], 0)
        self.assertFalse(first['exhausted'])
        self.assertEqual(self.inferences, 0)
        second = self.generate()
        self.assertEqual(calls, [None, cursor])
        self.assertEqual(second['processed'], 1)
        self.assertEqual(self.inferences, 1)

    def test_model_runtime_drift_refuses_submission_and_retains_candidate(self):
        runtime = self.ai.nodes['local']; original = runtime.inventory
        def infer(*args, **kwargs):
            self.inferences += 1
            runtime.inventory = lambda **kw: dict(original(), runtime={'version':'changed-after-inference'})
            return {'content':'{"text":"PRIVATE_DURABLE_CANDIDATE"}'}
        self.ai.infer = infer
        from interlinked_history_service import MemoryRunDrift
        with self.assertRaises(MemoryRunDrift): self.generate()
        self.assertEqual(self.primary.store.memories(), [])
        self.assertEqual(self.current_run()['status'], 'drift')
        self.assertEqual(len(list((self.history.root/'memory-drafts').glob('*.json'))), 1)

    def test_retagging_reopens_an_exhausted_project_queue(self):
        project = {'project':'Newly tagged project'}
        empty = self.history.generate_memories(project, FastEvent(), self.progress.append)
        self.assertTrue(empty['exhausted'])
        self.assertEqual(empty['processed'], 0)
        self.primary.store.assign_project(self.rows[0]['id'], project['project'])
        result = self.history.generate_memories(project, FastEvent(), self.progress.append)
        self.assertEqual(result['newRunReason'], 'source_selection_changed')
        self.assertEqual(result['processed'], 1)
        self.assertEqual(self.inferences, 1)
        self.assertEqual(len(self.primary.store.memories()), 1)

    def test_older_peer_observation_cannot_overwrite_newer_progress(self):
        now = time.monotonic()
        self.history._cache_peer({'jobs':[{'status':'completed', 'records':100}]}, now)
        self.history._cache_peer({'jobs':[{'status':'running', 'records':1}]}, now-1)
        state = self.history.state()
        self.assertEqual(state['jobs'][-1]['records'], 100)
        self.assertFalse(state['peerStateStale'])

    def test_background_progress_ticks_preserve_three_hundred_second_full_sync(self):
        import interlinked_history_service as service
        clock = [0.0]
        class Stops(FastEvent):
            def __init__(self): super().__init__(); self.steps=iter((1,1,298,1,1))
            def wait(self, seconds):
                try: clock[0] += next(self.steps)
                except StopIteration: self.set()
                return self.is_set()
        original_stop = self.history.stop
        self.history.stop = Stops()
        try:
            with mock.patch.object(service.time, 'monotonic', side_effect=lambda:clock[0]), \
                 mock.patch.object(self.history, 'sync') as sync, \
                 mock.patch.object(self.history, '_refresh_peer_async') as refresh:
                self.history._poll()
                self.assertEqual(sync.call_count, 2, 'Full sync should run at startup and after 300 seconds, not every progress tick.')
                self.assertGreater(refresh.call_count, sync.call_count)
        finally:
            self.history.stop = original_stop

    def test_remote_progress_refresh_is_nonblocking_singleflight_and_metadata_only(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        self.history.peer_state = {'jobs':[{'id':'remote', 'status':'running', 'phase':'normalizing', 'records':1}]}
        self.history.peer_observed_tick = time.monotonic()-20
        def delayed(action, body=None, cancel=None):
            calls.append((action, threading.current_thread().name)); entered.set()
            if not release.wait(5): raise AssertionError('Synthetic peer gate not released')
            return {'jobs':[{'id':'remote', 'status':'running', 'phase':'embedding', 'records':32, 'total':60}]}
        self.history._peer = delayed
        started = time.monotonic()
        state = self.history.state()
        self.assertLess(time.monotonic()-started, 1)
        self.assertTrue(entered.wait(1))
        self.assertTrue(state['peerRefreshInFlight'])
        try:
            for _ in range(5): self.history.state()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'progress', 'Lightweight progress refresh must not request full state or a snapshot.')
            self.assertNotEqual(calls[0][1], threading.current_thread().name)
        finally:
            release.set(); self.history.peer_refresh_thread.join(3)
        state = self.history.state()
        self.assertEqual(state['jobs'][-1]['records'], 32)
        self.assertIsNotNone(state['lastPeerObservedAt'])
        self.assertFalse(state['peerStateStale'])

    def test_failed_refresh_retains_cached_progress_and_reports_staleness(self):
        self.history.peer_state = {'jobs':[{'id':'remote', 'status':'running', 'records':12}]}
        self.history.last_peer_observed_at = '2026-11-02T00:00:00+00:00'
        self.history.peer_observed_tick = time.monotonic()-20
        def failed(*args, **kwargs): raise RuntimeError('PRIVATE_TRANSPORT_DIAGNOSTIC')
        self.history._peer = failed
        self.history.state(); self.history.peer_refresh_thread.join(3)
        state = self.history.state()
        self.assertTrue(state['peerStateStale'])
        self.assertEqual(state['jobs'][-1]['records'], 12)
        self.assertEqual(state['lastPeerObservedAt'], '2026-11-02T00:00:00+00:00')
        self.assertNotIn('PRIVATE_TRANSPORT_DIAGNOSTIC', json.dumps(state))

    def test_selection_race_refuses_old_cursor_and_retry_recovers_earlier_source(self):
        earlier = min(self.rows, key=lambda row:row['id'])
        self.primary.store.exclude(earlier['id'])
        self.generate()
        queue_path = next((self.history.root/'memory-queues').glob('*.json'))
        before = queue_path.read_bytes()
        changed = [False]
        def raced(action, body=None, cancel=None):
            if action=='memory-inputs' and body.get('after') and not changed[0]:
                changed[0] = True
                self.primary.store.exclude(earlier['id'], False)
            return self.peer(action, body, cancel)
        self.history._peer = raced
        from interlinked_history_service import MemoryRunDrift
        with self.assertRaises(MemoryRunDrift): self.generate()
        self.assertEqual(queue_path.read_bytes(), before)
        self.assertEqual(self.inferences, 20)
        result = self.generate()
        self.assertEqual(result['newRunReason'], 'source_selection_changed')
        self.assertEqual(self.inferences, 21)
        self.assertTrue(any(ref['id']==earlier['id'] for memory in self.primary.store.memories() for ref in memory['sources']))

    def test_progress_rpc_never_reads_corpus_status_or_exclusions(self):
        with mock.patch.object(self.primary.store, 'status', side_effect=AssertionError('Full status scan forbidden')), \
             mock.patch.object(self.primary.store, 'exclusions', side_effect=AssertionError('Full corpus scan forbidden')):
            value = self.peer('progress')
        self.assertEqual(set(value), {'jobs','preparedGenerations'})
        self.assertNotIn('PRIVATE_DURABLE_CANDIDATE', json.dumps(value))

    def test_newer_progress_does_not_discard_full_state_controls(self):
        now = time.monotonic()
        self.history._cache_peer({'jobs':[{'status':'running','records':20}], 'preparedGenerations':[]}, now+1, progress_only=True)
        self.history._cache_peer({'jobs':[{'status':'running','records':1}], 'preparedGenerations':[], 'coverage':{'records':5}, 'available':True}, now)
        state = self.history.state()
        self.assertEqual(state['coverage'], {'records':5})
        self.assertEqual(state['jobs'][-1]['records'], 20)


if __name__=='__main__':unittest.main()
