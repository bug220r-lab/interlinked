"""Deterministic synthetic regressions for history intents, recovery and reuse."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from interlinked_history_store import HistoryStore, canonical, file_hash, json_write
from interlinked_history_service import HistoryController, HistoryNode, MemoryRunDrift


def key(value): return hashlib.sha256(value.encode()).hexdigest()


def record(name, text=None, conversation=None):
    text = text or ('owl preference ' + name)
    return dict(id=key(name),revision=key(text),kind='conversation',title='Synthetic',text=text,
                memory_allowed=True,metadata=dict(role='user',conversationId=conversation or name))


class CountingEmbedder:
    identity = dict(model='synthetic',digest='a'*64,dimensions=3)
    def __init__(self): self.documents = 0
    def __call__(self,texts):
        self.documents += len(texts)
        return [[1,0,0] if 'owl' in t else [0,1,0] for t in texts]
    def query(self,text): return [1,0,0]


class Repairs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-repairs-')
        self.root = Path(self.temp.name)
        self.primary = HistoryStore(self.root/'primary')
        self.rows = [record('one',conversation='shared'),record('two',conversation='shared')]
        self.primary.activate(self.primary.build(self.rows)['generationId'])
        self.replica = HistoryStore(self.root/'replica',role='replica')
        self.replicate()
    def tearDown(self): self.temp.cleanup()
    def replicate(self):
        manifest = self.primary.snapshot_manifest()
        self.replica.install_snapshot(manifest,lambda p,o,n:self.primary.snapshot_read_chunk(manifest['snapshotId'],p,o,n))
    def queue(self,source,enabled):
        self.replica.queue_exclusion(self.replica.exclusion_binding(source['id']),enabled,self.replica.reserve_exclusion())
        return next(p for p in self.replica.exclusion_intents() if p['binding']['sourceId']==source['id'])
    def test_stale_response_cannot_clear_newer_shared_rule_intent(self):
        old = self.queue(self.rows[0],False)
        newer = self.queue(self.rows[1],True)
        response = self.primary.apply_exclusion_intent(old,'pair-fixture')
        self.replica.acknowledge_exclusion(old,response)
        self.assertEqual(self.replica.exclusion_intents(),[newer])
        self.assertIsNone(self.replica.source(self.rows[0]['id']))
        self.primary.apply_exclusion_intent(newer,'pair-fixture')
        stale = self.primary.apply_exclusion_intent(old,'pair-fixture')
        self.assertEqual(stale['status'],'stale')
        self.assertIsNone(self.primary.source(self.rows[0]['id']))
        self.assertEqual(self.primary.apply_exclusion_intent(old,'new-authenticated-pair')['status'],'stale')
    def test_late_resolution_uses_reserved_ordinal_without_overriding_latest(self):
        old_ordinal = self.replica.reserve_exclusion()
        self.queue(self.rows[1],True)
        self.replica.queue_exclusion(self.replica.exclusion_binding(self.rows[0]['id']),False,old_ordinal)
        self.assertTrue(self.replica.exclusion_intents()[0]['enabled'])
        self.assertIsNone(self.replica.source(self.rows[0]['id']))
    def test_restarts_keep_intent_and_primary_receipt_and_reject_pair_spoof(self):
        payload = self.queue(self.rows[0],True)
        result = self.primary.apply_exclusion_intent(payload,'pair-fixture')
        restarted = HistoryStore(self.primary.root)
        self.assertEqual(restarted.apply_exclusion_intent(payload,'pair-fixture'),result)
        replica = HistoryStore(self.replica.root,role='replica')
        self.assertEqual(replica.exclusion_intents(),[payload])
        changed = copy.deepcopy(payload); changed['enabled']=False
        changed['fingerprint']=key(canonical({k:v for k,v in changed.items() if k!='fingerprint'}).decode())
        with self.assertRaises(ValueError): restarted.apply_exclusion_intent(changed,'pair-fixture')
        with self.assertRaisesRegex(ValueError,'fenced'):
            restarted.exclude(self.rows[0]['id'],False,caller_identity='pair-fixture')
        changed = copy.deepcopy(payload); changed['replicaId']='f'*32
        changed['fingerprint']=hashlib.sha256(canonical({k:v for k,v in changed.items() if k!='fingerprint'})).hexdigest()
        with self.assertRaises(ValueError): restarted.apply_exclusion_intent(changed,'pair-fixture')
    def test_reimport_does_not_rebind_frozen_intent(self):
        payload = self.queue(self.rows[0],True)
        original = copy.deepcopy(payload['binding'])
        changed = record('one','Changed source',conversation='another')
        self.primary.activate(self.primary.build([changed])['generationId'])
        self.primary.apply_exclusion_intent(payload,'pair-fixture')
        self.assertEqual(payload['binding'],original)
        self.assertIsNotNone(self.primary.source(changed['id']))
    def test_legacy_unavailable_and_conflicting_choices_preserve_local_block(self):
        target = HistoryStore(self.root/'legacy',role='replica')
        manifest=self.primary.snapshot_manifest()
        target.install_snapshot(manifest,lambda p,o,n:self.primary.snapshot_read_chunk(manifest['snapshotId'],p,o,n))
        target.exclude(self.rows[0]['id'],True)
        target.migrate_exclusions({self.rows[0]['id']:False,'f'*64:True})
        self.assertEqual(target.exclusion_sync_state()['unresolvedCount'],2)
        self.assertIsNone(target.source(self.rows[0]['id']))
        before=target.exclusion_sync_state()
        target.migrate_exclusions({})
        self.assertEqual(target.exclusion_sync_state(),before)
    def test_same_generation_snapshots_leave_no_duplicate_stages(self):
        for i in range(3):
            self.primary.assign_project(self.rows[0]['id'],'project'+str(i));self.replicate()
        self.assertEqual(len(list((self.replica.root/'generations').iterdir())),1)
        self.assertEqual(list((self.replica.root/'sync-stages').iterdir()),[])
    def test_stage_failure_preserves_active_and_cleans_only_owned_stage(self):
        old = self.replica.generation_info()['generationId']
        unknown=self.replica.root/'generations'/('.'+'f'*32);unknown.mkdir();(unknown/'unknown.txt').write_text('keep')
        manifest=self.primary.snapshot_manifest()
        with self.assertRaises(ValueError): self.replica.install_snapshot(manifest,lambda p,o,n:b'x'*n)
        self.assertEqual(self.replica.generation_info()['generationId'],old)
        self.assertTrue((unknown/'unknown.txt').exists())
        self.assertEqual(list((self.replica.root/'sync-stages').iterdir()),[])
    def test_crash_owned_stage_recovered_but_unmarked_stage_preserved(self):
        stage_id='a'*32;stage=self.replica.root/'generations'/('.'+stage_id);stage.mkdir()
        (stage/'index.sqlite3').write_bytes(b'synthetic interrupted transfer')
        json_write(self.replica.root/'sync-stages'/(stage_id+'.json'),dict(format='interlinked-history-sync-stage-v1',root=str(self.replica.root),stage=stage.name,snapshotId='b'*64))
        keep=self.replica.root/'generations'/('.'+'c'*32);keep.mkdir()
        HistoryStore(self.replica.root,role='replica')
        self.assertFalse(stage.exists());self.assertTrue(keep.exists())

    def test_damaged_existing_generation_keeps_verified_recovery_stage_and_policy(self):
        active=(self.replica.root/'ACTIVE.json').read_bytes()
        controls=file_hash(self.replica.controls_path)
        generation=self.replica.generation_info()['generationId']
        target=self.replica.root/'generations'/generation/'index.sqlite3'
        target.write_bytes(b'corrupt retained index')
        self.primary.assign_project(self.rows[0]['id'],'new-primary-project')
        with self.assertRaisesRegex(ValueError,'preserved for explicit recovery'):self.replicate()
        self.assertEqual((self.replica.root/'ACTIVE.json').read_bytes(),active)
        self.assertEqual(file_hash(self.replica.controls_path),controls)
        receipts=list((self.replica.root/'sync-stages').glob('*.json'))
        self.assertEqual(len(receipts),1)
        receipt=json.loads(receipts[0].read_text());self.assertEqual(receipt['phase'],'recovery_required')
        stage=self.replica.root/'generations'/receipt['stage']
        self.assertTrue((stage/'index.sqlite3').exists())
        HistoryStore(self.replica.root,role='replica')
        self.assertTrue(stage.exists(),'Restart must preserve verified recovery evidence')

    def test_damaged_existing_vectors_do_not_commit_new_controls(self):
        embedder=CountingEmbedder();primary=HistoryStore(self.root/'vector-primary',embedder=embedder)
        primary.activate(primary.build([record('vector')])['generationId'])
        replica=HistoryStore(self.root/'vector-replica',role='replica')
        def replicate():
            manifest=primary.snapshot_manifest()
            replica.install_snapshot(manifest,lambda p,o,n:primary.snapshot_read_chunk(manifest['snapshotId'],p,o,n))
        replicate();active=(replica.root/'ACTIVE.json').read_bytes();controls=file_hash(replica.controls_path)
        generation=replica.generation_info()['generationId']
        path=replica.root/'generations'/generation/'vectors.npy';raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        primary.assign_project(record('vector')['id'],'updated')
        with self.assertRaisesRegex(ValueError,'preserved for explicit recovery'):replicate()
        self.assertEqual((replica.root/'ACTIVE.json').read_bytes(),active)
        self.assertEqual(file_hash(replica.controls_path),controls)
    def test_memory_listing_batches_validation_and_pages_effective_status(self):
        for i in range(21): self.primary.propose_memory('memory '+str(i),[self.rows[0]['id']])
        with mock.patch.object(self.primary,'_index',wraps=self.primary._index) as index:
            page=self.primary.memories_page(20)
        self.assertEqual(index.call_count,1)
        self.assertEqual(len(page['memories']),20);self.assertEqual(page['total'],21)
        last=self.primary.memories_page(20,page['nextCursor'])
        self.assertEqual(len(last['memories']),1)
        self.primary.exclude(self.rows[0]['id'])
        self.assertTrue(all(m['effectiveStatus']=='blocked' for m in self.primary.memories_page()['memories']))
    def test_embedding_reuse_20_then_zero_then_one_and_identity_invalidation(self):
        embedder=CountingEmbedder();store=HistoryStore(self.root/'vectors',embedder=embedder)
        rows=[record('vector'+str(i)) for i in range(20)]
        first=store.build(rows);store.activate(first['generationId']);self.assertEqual(embedder.documents,20)
        embedder.documents=0;second=store.build(rows);store.activate(second['generationId'])
        self.assertEqual(embedder.documents,0);self.assertEqual(second['reusedVectors'],20)
        rows[0]=record('vector0','new owl source')
        third=store.build(rows);store.activate(third['generationId']);self.assertEqual(embedder.documents,1)
        self.assertTrue(store.search('owl'))
        embedder.documents=0;embedder.identity=dict(embedder.identity,digest='b'*64)
        store.build(rows);self.assertEqual(embedder.documents,20)

    def test_corrupt_prior_vectors_rebuild_instead_of_reusing_unverified_bytes(self):
        embedder=CountingEmbedder();store=HistoryStore(self.root/'corrupt-vectors',embedder=embedder)
        first=store.build([record('v1'),record('v2')]);store.activate(first['generationId'])
        path=store.root/'generations'/first['generationId']/'vectors.npy'
        raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        embedder.documents=0;result=store.build([record('v1'),record('v2')])
        self.assertEqual(embedder.documents,2);self.assertEqual(result['reusedVectors'],0)
        self.assertEqual(store.generation_info()['generationId'],first['generationId'])


class MemoryRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='interlinked-memory-repair-');self.root=Path(self.temp.name)
        self.ai=SimpleNamespace(runtime=SimpleNamespace(state=self.root/'app',demo=True),nodes={})
        self.history=HistoryController(self.ai,root=self.root/'client')
        self.node=HistoryNode(self.root/'node',SimpleNamespace())
        self.rows=[record('memory'+str(i)) for i in range(3)]
        self.node.store.activate(self.node.store.build(self.rows)['generationId'])
        self.history._peer=lambda a,b=None,cancel=None:self.node.execute(a,b or {},cancel,caller_identity='fixture-pair')
        self.history.choose_model=lambda *a:dict(host='local',id='fixture',digest='a'*64)
        self.history._memory_header=lambda m,p,g:dict(generationId=g,project=p,model=m)
        self.calls=0
        def infer(*a,**kw):self.calls+=1;return {'content':'{"text":"synthetic preference"}'}
        self.ai.infer=infer
    def tearDown(self): self.history.close();self.node.close();self.temp.cleanup()
    def generate(self,**body):
        event=threading.Event()
        with mock.patch.object(event,'wait',side_effect=lambda n:event.is_set()):
            return self.history.generate_memories(body,event,lambda v:None)
    def test_live_primary_restore_ignores_stale_primary_replica_rule(self):
        source=self.rows[0];self.node.store.exclude(source['id']);self.history.sync()
        self.node.store.exclude(source['id'],False)
        self.generate()
        self.assertEqual(self.calls,3)
        self.assertEqual(len(self.node.store.memories()),3)
    def test_uncertain_inference_requires_exact_explicit_retry_after_restart(self):
        def uncertain(*a,**kw): self.calls+=1;raise ConnectionError('synthetic lost answer')
        self.ai.infer=uncertain
        with self.assertRaises(ConnectionError):self.generate()
        with self.assertRaisesRegex(ValueError,'[Uu]ncertain'):self.generate()
        self.assertEqual(self.calls,1)
        attempts=list((self.history.root/'memory-attempts').glob('*.json'));request_id=json.loads(attempts[0].read_text())['requestId']
        self.ai.infer=lambda *a,**kw:{'content':'{"text":null}'}
        self.generate(retryUncertainRequestIds=[request_id])
        self.assertEqual(len(self.node.store.memories()),0)
    def test_legacy_queue_reset_reuses_completed_candidates_and_recovers_exhaustion(self):
        self.generate();self.assertEqual(self.calls,3)
        path=next((self.history.root/'memory-queues').glob('*.json'))
        queue=json.loads(path.read_text());queue.pop('eligibilityVersion');json_write(path,queue)
        self.generate();self.assertEqual(self.calls,3)
        self.assertEqual(len(self.node.store.memories()),3)
        self.assertEqual(len(list((path.parent/'migrations').iterdir())),1)
    def test_primary_batch_is_bounded_and_initial_validation_is_shared(self):
        actions=[];peer=self.history._peer
        def counted(a,b=None,cancel=None): actions.append(a);return peer(a,b,cancel)
        self.history._peer=counted;self.generate()
        self.assertEqual(actions.count('memory-sources'),1)
        self.assertEqual(actions.count('memory-source'),3)

    def test_committed_policy_change_after_early_reservation_fences_submission(self):
        ordinal=self.history.store.reserve_exclusion()
        before=self.history.store.local_exclusion_policy()[1]
        binding=self.node.store.exclusion_binding(self.rows[0]['id'])
        self.assertEqual(self.history.store.local_exclusion_policy()[1],before)
        def infer(*a,**kw):
            self.calls+=1
            self.history.store.queue_exclusion(binding,True,ordinal)
            return {'content':'{"text":"synthetic preference"}'}
        self.ai.infer=infer
        with self.assertRaises(MemoryRunDrift):self.generate()
        self.assertGreater(self.history.store.local_exclusion_policy()[1],before)
        self.assertEqual(self.node.store.memories(),[])

    def test_prepared_resolution_blocks_activation_until_acknowledged(self):
        prepared=record('prepared')
        generation=self.node.store.build([prepared])['generationId']
        old=self.node.store.generation_info()['generationId']
        entered,release=threading.Event(),threading.Event();errors=[];peer=self.history._peer
        def delayed(action,body=None,cancel=None):
            if action=='exclusion-binding':
                entered.set()
                if not release.wait(5):raise AssertionError('resolution gate timed out')
            return peer(action,body,cancel)
        self.history._peer=delayed
        def exclude():
            try:self.history.post('exclude',dict(id=prepared['id'],generation=generation,enabled=True))
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=exclude);thread.start();self.assertTrue(entered.wait(5))
        try:
            with self.assertRaisesRegex(ValueError,'exclusions'):self.history.post('activate',{'generation':generation})
            self.assertEqual(self.node.store.generation_info()['generationId'],old)
        finally:release.set();thread.join(5)
        self.assertFalse(errors,errors)
        self.history.post('activate',{'generation':generation})
        self.assertEqual(self.node.store.generation_info()['generationId'],generation)
        self.assertIsNone(self.node.store.source(prepared['id']))

    def test_qualification_change_never_relabels_unsubmitted_candidate(self):
        qualification={'modelTag':'alias-one','policySha256':None}
        self.history._memory_header=lambda m,p,g:dict(generationId=g,project=p,model=m,qualificationFingerprint=dict(qualification))
        def drift(*a,**kw):
            self.calls+=1;qualification['modelTag']='alias-two'
            return {'content':'{"text":"old qualified candidate"}'}
        self.ai.infer=drift
        with self.assertRaises(MemoryRunDrift):self.generate()
        self.assertEqual(self.calls,1)
        with self.assertRaisesRegex(ValueError,'review'):self.generate()
        self.assertEqual(self.calls,1)
        request_id=next(p.stem for p in (self.history.root/'memory-attempts').glob('*.json') if len(p.stem)==32)
        def fresh(*a,**kw):self.calls+=1;return {'content':'{"text":"new qualified candidate"}'}
        self.ai.infer=fresh
        self.generate(retryUncertainRequestIds=[request_id]);self.assertEqual(self.calls,4)
        qualification['modelTag']='alias-three'
        self.generate();self.assertEqual(self.calls,4)
        self.assertEqual(len(self.node.store.memories()),3)

    def test_durable_candidate_and_submitted_proposal_resolve_old_uncertainty(self):
        self.generate()
        paths=list((self.history.root/'memory-attempts').glob('*.json'))
        for path in paths:
            value=json.loads(path.read_text());value['phase']='inference_started';json_write(path,value)
        self.assertTrue(self.history.state()['memoryRecovery']['uncertainRequestIds'])
        queue=next((self.history.root/'memory-queues').glob('*.json'))
        value=json.loads(queue.read_text());value.pop('eligibilityVersion');json_write(queue,value)
        self.generate()
        self.assertEqual(self.calls,3)
        self.assertEqual(self.history.state()['memoryRecovery']['uncertainRequestIds'],[])


if __name__=='__main__':unittest.main()
