"""Deterministic worker recovery, qualification and state-cost regressions.

All transports, runtimes and projects are synthetic; no model/server is contacted.
"""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

import interlinked_ai as ai
import interlinked_ai_node as node
import interlinked_ai_workspaces as ws
import test_ai_routing as routing_fixture


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        (self.source/'example.py').write_text('answer = 1\n')
        self.local = ws.Workspaces(self.root/'local', lambda:[dict(id='fixture',root=str(self.source))], acquire=lambda _:nullcontext())
        self.local.approve('fixture')
        self.remote = ws.Workspaces(self.root/'remote')
        self.dispatch = node.WorkerDispatcher(self.remote)
        self.identity = dict(computer='FIXTURE',sid='fixture')
        self.server = node.NodeService(SimpleNamespace(identity=self.identity),self.root/'server',protector=lambda b,**_:b,worker_dispatch=self.dispatch)
        outer = self
        class Peer:
            worker_operation_recovery = True
            identity, fingerprint = outer.identity, 'fixture-certificate'
            drop = None
            calls = []
            def execute(self,action,payload,request_id=None,**kwargs):
                self.calls.append(action)
                result = outer.server.rpc(dict(protocol=node.PROTOCOL,identity=self.identity,requestId=request_id or uuid.uuid4().hex,action=action,payload=payload))['result']
                if self.drop == action:
                    self.drop = None
                    raise OSError('fixture committed response lost')
                return result
        self.peer = Peer()
    def tearDown(self):
        self.local.close(); self.remote.close(); self.tmp.cleanup()
    def body(self):
        return dict(projectId='fixture',prompt='Read fixture',requestId=uuid.uuid4().hex,
                    model=dict(host='local',id='fixture',digest='a'*64),permissions=['read'],timeoutSeconds=30)
    def wait(self,row):
        self.local.threads[row['id']].join(4)
        return self.local.task(row['id'])
    def start_lost(self,action):
        self.peer.drop = action
        return self.wait(self.local.start_remote(self.body(),self.peer,lambda *args:dict(content='fixture')))
    def test_lost_start_is_recoverable_and_cancelled_without_replay(self):
        row = self.start_lost('worker_start')
        self.assertEqual(row['status'],'RECOVERY_REQUIRED')
        self.assertIsNone(row.get('remoteTaskId'))
        self.assertEqual(len(self.remote.state['tasks']),1)
        row = self.local.reconcile(row['id'],self.peer)
        self.assertEqual(row['status'],'RECOVERY_REQUIRED')
        self.assertIn(row['remoteTaskId'],self.remote.state['tasks'])
        for _ in range(30):
            row = self.local.reconcile(row['id'],self.peer,cancel=True)
            if row['status'] == 'CANCELED': break
            time.sleep(.02)
        self.assertEqual(row['status'],'CANCELED',row)
        self.assertEqual(self.peer.calls.count('worker_start'),1)
        self.assertEqual(self.peer.calls.count('worker_prepare'),1)
        self.assertNotIn('worker_infer_result',self.peer.calls)
    def test_lost_prepare_recovers_same_import_and_never_starts(self):
        row = self.start_lost('worker_prepare')
        self.assertEqual(row['status'],'RECOVERY_REQUIRED')
        self.assertEqual(len(self.remote.state['imported']),1)
        row = self.local.reconcile(row['id'],self.peer,cancel=True)
        self.assertEqual(row['status'],'CANCELED')
        self.assertIn(row['remoteOperation']['prepare']['importedProjectId'],self.remote.state['imported'])
        self.assertFalse(self.remote.state['tasks'])
    def test_reconcile_serializes_stale_query_before_new_cancellation(self):
        row=self.start_lost('worker_prepare'); ident=row['id']
        queried,release,cancel_entered=threading.Event(),threading.Event(),threading.Event()
        original=self.peer.execute; results={}; errors=[]
        def execute(action,payload,**kwargs):
            answer=original(action,payload,**kwargs)
            if action=='operation_query': queried.set(); release.wait(2)
            return answer
        self.peer.execute=execute
        def run(name,cancel=False):
            if cancel: cancel_entered.set()
            try: results[name]=self.local.reconcile(ident,self.peer,cancel=cancel)
            except Exception as exc: errors.append(exc)
        older=threading.Thread(target=run,args=('read',)); older.start()
        self.assertTrue(queried.wait(1))
        newer=threading.Thread(target=run,args=('cancel',True)); newer.start()
        self.assertTrue(cancel_entered.wait(1)); release.set()
        older.join(3); newer.join(3)
        self.assertFalse(errors,errors); self.assertFalse(older.is_alive()); self.assertFalse(newer.is_alive())
        final=self.local.task(ident)
        self.assertEqual(final['status'],'CANCELED')
        self.assertTrue(final['remoteOperation']['cancelRequested'])
        self.assertEqual(self.peer.calls.count('worker_prepare'),1)
        self.assertNotIn('worker_start',self.peer.calls)
    def test_reconcile_rejects_live_owner_without_changing_task(self):
        entered=threading.Event(); release=threading.Event()
        def infer(*args): entered.set(); release.wait(2); return dict(content='fixture')
        row=self.local.start(self.body(),infer)
        self.assertTrue(entered.wait(1)); before=self.local.task(row['id'])
        try:
            with self.assertRaisesRegex(ValueError,'still active'):
                self.local.reconcile(row['id'],self.peer)
            self.assertEqual(self.local.task(row['id']),before)
        finally: release.set(); self.local.threads[row['id']].join(3)
    def test_restart_and_lost_cancel_ack_preserve_original_operation(self):
        row=self.start_lost('worker_start'); ident=row['id']
        self.local.close()
        self.local=ws.Workspaces(self.root/'local',lambda:[dict(id='fixture',root=str(self.source))],acquire=lambda _:nullcontext())
        self.peer.drop='operation_cancel'
        row=self.local.reconcile(ident,self.peer,cancel=True)
        self.assertEqual(row['status'],'RECOVERY_REQUIRED')
        for _ in range(30):
            row=self.local.reconcile(ident,self.peer,cancel=True)
            if row['status']=='CANCELED': break
            time.sleep(.02)
        self.assertEqual(row['status'],'CANCELED',row)
        self.assertEqual(self.peer.calls.count('worker_start'),1)
        self.assertEqual(len(self.remote.state['tasks']),1)
    def test_cancel_tombstone_fences_delayed_start_after_node_restart(self):
        payload = self.body()
        rpc_id = uuid.uuid4().hex
        binding = dict(targetRpcId=rpc_id,expectedAction='worker_start',expectedFingerprint=ws.operation_fingerprint('worker_start',payload))
        answer = self.server.operation(binding,cancel=True)
        self.assertEqual(answer['status'],'cancelled')
        self.server = node.NodeService(SimpleNamespace(identity=self.identity),self.root/'server',protector=lambda b,**_:b,worker_dispatch=self.dispatch)
        with self.assertRaisesRegex(ValueError,'cancelled'):
            self.peer.execute('worker_start',payload,request_id=rpc_id)
        self.assertFalse(self.remote.state['tasks'])
        with self.assertRaisesRegex(ValueError,'binding differs'):
            self.server.operation(dict(binding,expectedFingerprint='f'*64))
    def test_node_does_not_confirm_cancellation_of_uncertain_task(self):
        rpc_id,task_id=uuid.uuid4().hex,uuid.uuid4().hex
        fingerprint=ws.operation_fingerprint('worker_start',{})
        self.remote.state['tasks'][task_id]=dict(id=task_id,status='RECOVERY_REQUIRED',createdAt='fixture')
        self.remote._register_operation(dict(id=rpc_id,action='worker_start',fingerprint=fingerprint),dict(taskId=task_id))
        self.remote._save()
        answer=self.server.operation(dict(targetRpcId=rpc_id,expectedAction='worker_start',expectedFingerprint=fingerprint),cancel=True)
        self.assertEqual(answer['status'],'cancel_pending')
        self.assertTrue(answer['dispatchFenced'])
    def test_effect_survives_outer_receipt_failure_and_workspace_restart(self):
        payload = self.local.export_source('fixture'); rpc_id = uuid.uuid4().hex
        original = node.write_json
        def write(path,value):
            if Path(path).stem == rpc_id and value.get('status') == 'completed': raise OSError('fixture final receipt loss')
            return original(path,value)
        with mock.patch.object(node,'write_json',side_effect=write), self.assertRaises(OSError):
            self.peer.execute('worker_prepare',payload,request_id=rpc_id)
        self.remote.close()
        self.remote = ws.Workspaces(self.root/'remote')
        self.dispatch.workspaces = self.remote
        answer = self.server.operation(dict(targetRpcId=rpc_id,expectedAction='worker_prepare',expectedFingerprint=ws.operation_fingerprint('worker_prepare',payload)))
        self.assertEqual(answer['status'],'committed')
        self.assertIn(answer['effect']['projectId'],self.remote.state['imported'])
        with self.assertRaisesRegex(ValueError,'never replayed'):
            self.peer.execute('worker_prepare',payload,request_id=rpc_id)
        self.assertEqual(len(self.remote.state['imported']),1)
    def test_older_node_rejected_before_transfer(self):
        self.peer.worker_operation_recovery = False
        with self.assertRaisesRegex(ValueError,'Upgrade'):
            self.local.start_remote(self.body(),self.peer,lambda *args:None)
        self.assertFalse(self.peer.calls)
    def test_unchanged_remote_observations_do_not_rewrite_history(self):
        ident = uuid.uuid4().hex
        self.local.state['tasks'][ident] = dict(id=ident,status='RUNNING',checks=[],summary='',review=None,error=None)
        observation = dict(status='RUNNING',checks=[],summary='',review=None,error=None)
        observations=[]; self.local.observer=observations.append
        with mock.patch.object(self.local,'_save') as save:
            for _ in range(10): self.local._observe_remote(ident,observation)
            save.assert_not_called()
            self.local._observe_remote(ident,dict(observation,status='FAILED'))
            save.assert_called_once()
        self.assertIn('remoteObservedAt',self.local.task(ident))
        self.assertEqual(len(observations),11)
        self.assertIn('updatedAt',observations[0])
    def test_detail_revisions_cover_action_apply_and_restart_writes(self):
        payload=self.body(); payload['permissions']=['read','write']
        snapshots=[]; original=self.local._save
        def save():
            original()
            for task in self.local.state['tasks'].values():
                snapshots.append((task.get('revision',0),task['status'],json.dumps(task.get('actions',[]),sort_keys=True),tuple(task.get('appliedFiles',[]))))
        def infer(model,messages,tools,cancel):
            return dict(content='done') if messages[-1]['role']=='tool' else dict(content='',tool_calls=[dict(function=dict(name='write_file',arguments=dict(path='example.py',content='answer = 2\n')))])
        with mock.patch.object(self.local,'_save',side_effect=save):
            row=self.wait(self.local.start(payload,infer))
            before=row['revision']
            applied=self.local.apply(row['id'],row['result']['sha256'])
        self.assertGreater(applied['revision'],before)
        previous=None
        for snapshot in snapshots:
            if previous and snapshot[1:] != previous[1:]: self.assertGreater(snapshot[0],previous[0])
            previous=snapshot
        ident=uuid.uuid4().hex
        self.local.state['tasks'][ident]=dict(id=ident,status='RUNNING',revision=10,createdAt='fixture')
        self.local._save(); self.local.close()
        self.local=ws.Workspaces(self.root/'local',lambda:[dict(id='fixture',root=str(self.source))],acquire=lambda _:nullcontext())
        self.assertEqual(self.local.task(ident)['status'],'RECOVERY_REQUIRED')
        self.assertEqual(self.local.task(ident)['revision'],11)


class Qualification(unittest.TestCase):
    setUp = routing_fixture.RoutingTests.setUp
    tearDown = routing_fixture.RoutingTests.tearDown
    profile = routing_fixture.RoutingTests.profile
    save = routing_fixture.RoutingTests.save
    select = routing_fixture.RoutingTests.select
    def test_alias_does_not_inherit_calibration_or_measured_profile(self):
        inventory = self.service.hosts['local']['inventory']
        inventory['models'].append(dict(name='alias',digest='a'*64,size=1))
        models = {row['id']:row for row in self.service.models()}
        self.assertTrue(models['quality']['verified']['text'])
        self.assertFalse(models['alias']['verified'])
        self.assertFalse(self.service._profile_matches(self.evidence['profiles'][0],models['alias'],self.settings))
        self.assertEqual(self.select(routingMode='best_quality')[0]['id'],'quality')
    def test_legacy_capability_and_routing_receipts_remain_unqualified(self):
        prior = copy.deepcopy(self.service.calibration)
        self.service.calibration={'local:'+'a'*64:dict(text=dict(passed=True,benchmarkVersion=2))}
        self.assertFalse(self.service.model('local','quality')['verified'])
        self.assertEqual(self.service.calibration['local:'+'a'*64]['text']['benchmarkVersion'],2)
        self.service.calibration=prior
        self.evidence['format']='interlinked-routing-evidence-v1'; self.save()
        self.assertTrue(self.select()[1]['limitedEvidence'])
    def test_policy_change_invalidates_capability_trials(self):
        self.service.hosts['local']['inventory']['inferencePolicySha256']='f'*64
        self.assertFalse(self.service.model('local','quality')['verified'])
    def test_queued_selection_cannot_inherit_fresh_calibration_of_new_policy(self):
        selected=self.service.route({'text'},dict(model='quality'))
        inventory=self.service.nodes['local'].inventory(); inventory['inferencePolicySha256']='f'*64
        fingerprint=self.service.qualification(selected,inventory)
        trials=copy.deepcopy(self.service.calibration[ai.qualification_key(selected['qualificationFingerprint'])])
        for trial in trials.values(): trial['qualificationFingerprint']=fingerprint
        self.service.calibration[ai.qualification_key(fingerprint)]=trials
        with mock.patch.object(self.service.nodes['local'],'inventory',return_value=inventory):
            with self.assertRaisesRegex(ValueError,'Qualification identity changed'):
                self.service.infer(selected,[dict(role='user',content='fixture')])
        self.assertFalse(self.service.nodes['local'].calls)
    def test_history_qualification_uses_fixed_profile_budgets(self):
        fp=self.service.qualification(self.service.model('local','quality'),response_profile='history_v1')
        self.assertEqual((fp['contextTokens'],fp['responseTokens']),(16384,4096))
    def test_state_pages_are_compact_and_detail_is_retained(self):
        for i in range(70):
            ident=f'{i:032x}'
            self.service.workspaces.state['tasks'][ident]=dict(id=ident,requestId=ident,projectId='fixture',createdAt=f'{i:03}',status='READY_FOR_REVIEW',result=dict(content='x'*100000),baseline=dict(private='value'),actions=[dict(private='value')],prompt='p'*1000)
        state=self.service.state()
        self.assertEqual(len(state['tasks']),50)
        self.assertEqual(state['tasksPage']['total'],70)
        self.assertLess(len(json.dumps(state)),70000)
        second=self.service.get('tasks',dict(after=[state['tasksPage']['nextCursor']]))
        self.assertEqual(len(second['tasks']),20)
        self.assertIsNone(second['nextCursor'])
        self.assertEqual(len(self.service.task(state['tasks'][0]['id'])['result']['content']),100000)
    def test_outer_receipt_repair_is_persisted_without_start(self):
        request_id,task_id=uuid.uuid4().hex,uuid.uuid4().hex
        payload=dict(requestId=request_id,projectId='fixture',prompt='retained')
        self.service.workspaces.state['tasks'][task_id]=dict(id=task_id,requestId=request_id,requestHash=ws.sha(ws.encoded(payload)),projectId='fixture',status='RECOVERY_REQUIRED',createdAt='fixture')
        self.service.workspaces._save()
        self.service.task_requests[request_id]=dict(payload=payload,routingPhase='selected')
        self.service.write(self.service.root/'task-requests.json',self.service.task_requests)
        self.service.close()
        self.service=ai.AIService(SimpleNamespace(state=self.root,kit=self.root,demo=True),node_factory=routing_fixture.Node)
        self.assertEqual(self.service.read(self.service.root/'task-requests.json')[request_id]['id'],task_id)
        self.assertFalse(self.service.workspaces.threads)


class CapabilityCache(unittest.TestCase):
    def test_unknown_runtime_lifetime_does_not_cache_and_cancel_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            local=node.LocalNode(Path(tmp),dict(computer='FIXTURE',sid='fixture'))
            calls=[]
            def request(method,path,body=None,**kwargs):
                calls.append(path)
                return {'version':'fixture'} if path=='/api/version' else {'models':[dict(name='quality',digest='a'*64)]} if path=='/api/tags' else {'models':[]} if path=='/api/ps' else {'capabilities':['completion']}
            try:
                with mock.patch.object(local,'_request',side_effect=request),mock.patch.object(local,'capability_runtime_session',return_value=None),mock.patch.object(node,'resources',return_value={}):
                    local.inventory(); local.inventory()
                    self.assertEqual(calls.count('/api/show'),2)
                    cancel=threading.Event(); cancel.set(); before=len(calls)
                    with self.assertRaises(node.Cancelled): local.inventory(cancel=cancel)
                    self.assertEqual(len(calls),before)
                    self.assertFalse(local._capability_cache)
            finally: local.close()
    def test_exact_identity_cache_keeps_dynamic_inventory_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            local=node.LocalNode(Path(tmp),dict(computer='FIXTURE',sid='fixture'))
            calls=[]; identity=dict(tag='quality',digest='a'*64,version='fixture',session='1')
            def request(method,path,body=None,**kwargs):
                calls.append(path)
                return {'version':identity['version']} if path=='/api/version' else {'models':[dict(name=identity['tag'],digest=identity['digest'])]} if path=='/api/tags' else {'models':[]} if path=='/api/ps' else {'capabilities':['completion']}
            try:
                with mock.patch.object(local,'_request',side_effect=request),mock.patch.object(local,'capability_runtime_session',side_effect=lambda *_:identity['session']),mock.patch.object(node,'resources',return_value={}):
                    local.inventory(); local.inventory()
                    self.assertEqual(calls.count('/api/show'),1)
                    self.assertEqual(calls.count('/api/tags'),2)
                    self.assertEqual(calls.count('/api/ps'),2)
                    for key,value in [('tag','alias'),('digest','b'*64),('version','fixture2'),('session','2')]:
                        identity[key]=value; local.inventory()
                    local.inference_policy_sha256='f'*64; local.inventory()
                    self.assertEqual(calls.count('/api/show'),6)
            finally: local.close()


if __name__=='__main__': unittest.main()
