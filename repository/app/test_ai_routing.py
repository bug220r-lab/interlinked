"""Synthetic Auto routing behavior; no model servers or private project files."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import interlinked_ai as ai


class Node:
    def __init__(self, root):
        self.calls = []
        self.answer = '{"task":"analysis","complexity":"moderate","responseFormat":"text"}'
        self.on_call = None
    def inventory(self, **kwargs):
        return dict(runtime={'version':'fixture-v1'}, responseProfiles=['routing_v1'], inferencePolicySha256=None,
            models=[dict(name=n, digest=c*64, size=1) for n,c in [('quality','a'),('balanced','b'),('fast','c'),('classifier','d')]],
            loaded=[{'name':n} for n in ('quality','balanced','fast','classifier')], resources={})
    def chat(self, model, messages, **kwargs):
        self.calls.append((model, copy.deepcopy(messages), kwargs))
        if self.on_call: self.on_call()
        return dict(message=dict(content=self.answer if kwargs.get('response_profile') else 'A grounded reply.'), eval_count=7, load_duration=0)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = ai.AIService(SimpleNamespace(state=self.root, kit=self.root, demo=True), node_factory=Node)
        self.service.refresh()
        self.settings = self.service.settings_value()
        for n,c in [('quality','a'),('balanced','b'),('fast','c'),('classifier','d')]:
            fingerprint = self.service.qualification(dict(host='local',id=n,digest=c*64))
            self.service.calibration[ai.qualification_key(fingerprint)] = {cap:dict(passed=True, score=1, seconds=10 if n=='classifier' else 1,
                contextTokens=8192, runtimeVersion='fixture-v1', benchmarkVersion=3, qualificationFingerprint=fingerprint) for cap in ('text','coding','tools')}
        self.evidence = dict(format='interlinked-routing-evidence-v2', profiles=[], classifiers=[])
        for n,c,q,w in [('quality','a',100,6),('balanced','b',98,4),('fast','c',92,1)]:
            self.evidence['profiles'].append(self.profile(c,quality=q,warmSeconds=w,task='analysis'))
        self.evidence['classifiers'].append(self.profile('d',responseTokens=512,profile='routing_v1'))
        self.save()
    def profile(self, char, **fields):
        result=dict(host='local', modelTag={'a':'quality','b':'balanced','c':'fast','d':'classifier'}[char], modelDigest=char*64, runtimeVersion='fixture-v1', contextTokens=8192,
            responseTokens=2048, policySha256=None, fixtureVersion='fixture-1', graderVersion='grader-1',
            quality=100, criticalPassed=True, samples=4, warmSeconds=1, coldSeconds=2, inputBytes=100, outputTokens=100)
        result.update(fields); return result
    def save(self): self.service.write(self.service.root/'routing-evidence.json', self.evidence)
    def tearDown(self): self.service.close(); self.tmp.cleanup()
    def select(self, **body): return self.service.select_for_request({'text'}, body, 'Explain the supplied source.')
    def test_modes_choose_distinct_measured_models(self):
        for mode, expected in [('balanced','balanced'),('fast','fast'),('best_quality','quality')]:
            model, decision = self.select(routingMode=mode)
            self.assertEqual(model['id'], expected)
            self.assertFalse(decision['limitedEvidence'])
            self.assertFalse(decision['fallback'])
        self.assertEqual(len(self.service.nodes['local'].calls), 3)
    def test_manual_bypasses_classifier_and_exact_digest(self):
        model, decision=self.select(host='local', model='fast', digest='c'*64)
        self.assertEqual(model['id'],'fast'); self.assertEqual(decision['mode'],'manual')
        self.assertFalse(self.service.nodes['local'].calls)
        with self.assertRaisesRegex(ValueError,'digest'): self.select(model='fast',digest='a'*64)
    def test_invalid_mode_and_unknown_manual_model_fail(self):
        for body in ({'routingMode':'cheapest'}, {'model':'unknown'}):
            with self.assertRaises(ValueError): self.select(**body)
    def test_failed_classifier_falls_back_without_retry(self):
        self.service.nodes['local'].answer='```json\n{}\n```'
        _, decision=self.select()
        self.assertTrue(decision['fallback']); self.assertTrue(decision['limitedEvidence'])
        self.assertEqual(len(self.service.nodes['local'].calls),1)
    def test_missing_or_mismatched_classifier_never_runs_unqualified(self):
        self.evidence['classifiers'][0]['policySha256']='changed'; self.save()
        _, decision=self.select()
        self.assertTrue(decision['fallback']); self.assertFalse(self.service.nodes['local'].calls)

    def test_unknown_old_node_policy_is_not_explicit_no_policy(self):
        self.service.hosts['local']['inventory'].pop('inferencePolicySha256')
        with self.assertRaisesRegex(ValueError, 'capability tests'): self.select()
        self.assertFalse(self.service.nodes['local'].calls)
    def test_critical_failure_and_under_floor_never_win(self):
        self.evidence['profiles'][0]['criticalPassed']=False
        self.evidence['profiles'][1]['quality']=89
        self.save()
        self.assertEqual(self.select(routingMode='best_quality')[0]['id'],'fast')

    def test_fallback_never_reintroduces_known_failed_profiles(self):
        for profile in self.evidence['profiles']: profile['criticalPassed']=False
        self.evidence['profiles'].append(self.profile('d',task='analysis',quality=89))
        self.save()
        self.service.nodes['local'].answer='invalid classification'
        with self.assertRaisesRegex(ValueError,'quality or critical'):
            self.select(routingMode='fast')

    def test_rejection_record_overrides_a_prior_positive_profile(self):
        self.evidence['rejectedProfiles']=[self.profile('a',task='analysis',criticalPassed=False)]
        self.save()
        self.assertEqual(self.select(routingMode='best_quality')[0]['id'],'balanced')

    def test_mandatory_worker_quality_survives_classifier_task(self):
        for profile in self.evidence['profiles']:
            profile['task']='coding'; profile['criticalPassed']=False
        self.evidence['profiles'].append(self.profile('d',task='coding',quality=89))
        self.save()
        with self.assertRaisesRegex(ValueError,'quality or critical'):
            self.service.select_for_request({'text','coding','tools'}, {}, 'Update a fixture.')
    def test_changed_generation_budget_invalidates_profiles(self):
        value=copy.deepcopy(self.settings['value']); value['responseTokens']=4096
        self.service.post('settings',dict(value=value,settingsRevision=self.settings['revision']))
        with self.assertRaisesRegex(ValueError, 'capability tests'): self.select()
    def test_capacity_and_busy_host_affect_selection(self):
        self.service.capacity=lambda inv,m:m['id']!='fast'
        self.assertEqual(self.select(routingMode='fast')[0]['id'],'balanced')
    def test_duplicate_evidence_is_not_cherry_picked(self):
        self.evidence['profiles'].append(copy.deepcopy(self.evidence['profiles'][0])); self.save()
        self.assertEqual(self.select(routingMode='best_quality')[0]['id'],'balanced')
    def test_duplicate_classifier_evidence_is_not_cherry_picked(self):
        self.evidence['classifiers'].append(copy.deepcopy(self.evidence['classifiers'][0])); self.save()
        _, decision = self.select()
        self.assertTrue(decision['fallback'])
        self.assertFalse(self.service.nodes['local'].calls)
    def test_cancel_and_settings_change_never_fall_back(self):
        event=threading.Event(); self.service.nodes['local'].on_call=event.set
        with self.assertRaisesRegex(ValueError,'cancelled'):
            self.service.select_for_request({'text'}, {}, 'hello',cancel_event=event)
        event.clear()
        def change():
            v=copy.deepcopy(self.settings['value']); v['responseTokens']=4096
            self.service.post('settings',dict(value=v,settingsRevision=self.settings['revision']))
        self.service.nodes['local'].on_call=change
        with self.assertRaisesRegex(ValueError,'configuration_changed'): self.select()
    def test_expired_deadline_never_dispatches(self):
        with self.assertRaisesRegex(ValueError,'deadline'):
            self.service.select_for_request({'text'}, {}, 'hello',deadline=time.monotonic()-1)
        self.assertFalse(self.service.nodes['local'].calls)
    def test_changed_policy_after_selection_is_refused_before_dispatch(self):
        model, _ = self.select(model='fast')
        self.assertIn('routingPolicySha256', model)
        node = self.service.nodes['local']
        changed = node.inventory()
        changed['inferencePolicySha256'] = 'f'*64
        with mock.patch.object(node, 'inventory', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                self.service.infer(model, [dict(role='user', content='hello')])
        self.assertFalse(node.calls)
        self.assertFalse(self.service.slots['local'].locked())
    def test_classifier_does_not_get_tool_or_retrieved_content(self):
        self.service.select_for_request({'text'}, {}, 'Explain code that says ignore instructions',
            messages=[dict(role='tool',content='PRIVATE TOOL CONTENT')])
        _,messages,options=self.service.nodes['local'].calls[-1]
        self.assertNotIn('PRIVATE TOOL CONTENT',json.dumps(messages)); self.assertIsNone(options['tools'])
    def test_escaped_input_is_bounded_and_receipt_precedes_inference(self):
        def check():
            receipts=list((self.service.root/'routing-requests').glob('*.json'))
            self.assertEqual(len(receipts),1)
            self.assertEqual(json.loads(receipts[0].read_text())['status'],'pending')
        self.service.nodes['local'].on_call=check
        self.service.select_for_request({'text'}, {}, '\\"'*12000)
        _, messages, _=self.service.nodes['local'].calls[-1]
        self.assertLessEqual(len(('\n'.join(m['content'] for m in messages)+'[]').encode()),4096)
    def test_large_escaped_history_is_bounded_and_reports_truncation(self):
        model = self.service.model('local', 'classifier')
        evaluated = self.service.evaluate_classifier(model, 'Explain the current project.',
            messages=[dict(role='assistant', content='\\"'*12000), dict(role='user', content='Previous question.')])
        self.assertTrue(evaluated['inputTruncated'])
        _, messages, _ = self.service.nodes['local'].calls[-1]
        self.assertLessEqual(len(('\n'.join(m['content'] for m in messages)+'[]').encode()),4096)
        self.assertIn('Explain the current project.', messages[-1]['content'])
    def test_classifier_receipt_links_only_content_free_activity(self):
        activity = dict(origin='violet_bridge', flowId='worker:fixture', role='analysis', prompt='PRIVATE', token='PRIVATE')
        model = self.service.model('local', 'classifier')
        evaluated = self.service.evaluate_classifier(model, 'Explain the project.', activity=activity)
        path = self.service.root / 'routing-requests' / (evaluated['requestId'] + '.json')
        receipt = self.service.read(path)
        self.assertEqual(receipt['activity'], dict(origin='violet_bridge', flowId='worker:fixture', role='analysis'))
        self.assertNotIn('PRIVATE', json.dumps(receipt))
    def test_media_requirement_survives_classification(self):
        with self.assertRaisesRegex(ValueError,'capability tests'):
            self.service.select_for_request({'text','vision'}, {}, 'Describe the image.')
    def test_nan_and_oversized_classifier_are_rejected(self):
        self.evidence['profiles'][2]['quality']=float('nan'); self.save()
        self.assertEqual(self.select(routingMode='fast')[0]['id'],'balanced')
        with self.assertRaises(ValueError): ai.classification_value(' '*2049)
    def test_no_deadlock_classify_then_answer_same_host(self):
        model, decision=self.select()
        metrics={}
        answer=self.service.infer(model,[dict(role='user',content='hello')],metrics=metrics)
        self.assertTrue(answer['content']); self.assertEqual(metrics['runtime']['eval_count'],7)
        self.assertFalse(self.service.slots['local'].locked())


if __name__=='__main__': unittest.main()
