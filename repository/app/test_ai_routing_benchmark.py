"""Isolated benchmark contract checks; no runtime, model or project is touched."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import ai_routing_benchmark as b


def corpus():
    rows = []
    for project in sorted(b.PROJECTS):
        for i in range(6):
            excerpt = 'Dated project reference: a missing observation is not zero.'
            prompt = f'{project} question {i}.\n' + excerpt
            rows.append(dict(id=f'{project}-{i}', project=project,
                split='development' if i < 4 else 'holdout', provenanceKind='project-derived',
                task=list(sorted(b.TASKS))[i % 5], complexity='simple', responseFormat='json',
                prompt=prompt, promptSha256=b.sha(prompt),
                sources=[dict(path='C:/private/source.md', sha256='a'*64, startLine=1, endLine=1,
                              excerpt=excerpt, excerptSha256=b.sha(excerpt))],
                rubric=['Keeps missing separate from zero'], prohibitedClaims=['No test execution claim'],
                evaluator=dict(kind='exact_json', expected={'missing':None})))
    return dict(format=b.VERSION, fixtureVersion='isolated-v1', packets=rows)


def state(count=3):
    return dict(available=True, settingsRevision='fixture-r1', jobs=[],
        settings=dict(contextTokens=8192,responseTokens=2048),
        routingCapabilities=dict(version=1,modes=list(b.MODES),classifierProfile='routing_v1',classificationEvaluation=True,activityObservation=True),
        activity=dict(available=True,partial=False,activeCalls=[]),
        hosts=[dict(id='local',runtimeVersion='fixture-runtime',responseProfiles=['routing_v1'],inferencePolicySha256='f'*64)],
        models=[dict(host='local',id=f'model-{i}',digest=str(i)*64,eligible=True,stale=False,loaded=True,
                     verified=dict(text=True,coding=True,tools=True),scores=dict(text=1,coding=1,tools=1)) for i in range(count)])


TOOLS = [dict(name='submit_analysis',inputSchema=dict(properties=dict(routingMode=dict(enum=list(b.MODES))))),
         dict(name='classify_prompt',inputSchema={})]


def resources(s, now=1000):
    return dict(observedAt=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
                validUntil=dt.datetime.fromtimestamp(now+600,dt.timezone.utc).isoformat(),
                stateIdentitySha256=b.sha(b.identity(s)),observedBy='isolated test only',
                otherClientsIdle=True,activeProjectJobsPreserved=True,hosts=['local','msi'])


class FakeBridge:
    def __init__(self, s, answer='{"missing":null}', fail_submit=False):
        self.s, self.answer, self.fail_submit = s, answer, fail_submit
        self.calls = []

    def tools(self):
        return TOOLS

    def call(self,name,args):
        self.calls.append((name,copy.deepcopy(args)))
        if name == 'get_ai_state': return copy.deepcopy(self.s)
        if name in ('submit_analysis','classify_prompt'):
            if self.fail_submit: raise b.Hold('lost acknowledgement')
            return dict(id='fixture-job',status='queued')
        if name == 'get_job': return dict(id='fixture-job',status='completed')
        if name == 'get_result': return dict(result=dict(answer=self.answer,answerTruncated=False),provenance={})
        raise AssertionError(name)


class Contracts(unittest.TestCase):
    def test_corpus_freeze_and_split(self):
        c = corpus()
        self.assertEqual(b.validate_corpus(c)['holdout'],10)
        c['packets'][0]['prompt'] += 'changed'
        with self.assertRaises(ValueError): b.validate_corpus(c)

    def test_source_hash_and_excerpt_required(self):
        c=corpus();c['packets'][0]['sources'][0]['excerptSha256']='0'*64
        with self.assertRaises(ValueError): b.validate_corpus(c)

    def test_exact_thirty_and_project_splits(self):
        c=corpus();c['packets'][0]['split']='holdout'
        with self.assertRaises(ValueError): b.validate_corpus(c)
        c=corpus();c['packets'].pop()
        with self.assertRaises(ValueError): b.validate_corpus(c)

    def test_plan_budget_and_holdout_only_replay(self):
        c=corpus();p=b.make_plan(c,state())
        self.assertEqual(p['plannedCallCeiling'],430)
        self.assertEqual(sum(u['phase']=='manual' for u in p['units']),180)
        self.assertEqual(sum(u['phase']=='replay' for u in p['units']),60)
        self.assertEqual(sum(u['phase']=='classifier' for u in p['units']),120)
        self.assertEqual(sum(u['phase']=='cold_observation' for u in p['units']),10)
        packets={r['id']:r for r in c['packets']}
        self.assertTrue(all(packets[u['packetId']]['split']=='holdout' for u in p['units'] if u['phase']=='replay'))
        self.assertTrue(all(packets[u['packetId']]['split']=='development' for u in p['units'] if u['phase']=='classifier'))

    def test_evaluator_and_labels_never_sent(self):
        c=corpus();p=b.make_plan(c,state())
        for u in p['units']:
            self.assertFalse({'evaluator','rubric','expected','task','complexity','split'} & set(u['arguments']))
            if u['phase']=='classifier':
                self.assertEqual(set(u['arguments']),{'text','host','model','requestId'})
            else:
                self.assertEqual(u['arguments']['contextScope'],'supplied_text_only')

    def test_candidates_distinct_digests_and_unavailable_held(self):
        s=state(5);s['models'].append(dict(s['models'][0],host='msi'))
        groups=b.candidate_groups(s)
        self.assertTrue(all(len(v)==3 and len({r['digest'] for r in v})==3 for v in groups.values()))
        for row in s['models']: row['eligible']=False
        self.assertEqual(b.make_plan(corpus(),s)['missingCategories'],sorted(b.TASKS))

    def test_capability_and_actual_tool_schema_both_required(self):
        self.assertTrue(b.readiness(state(),TOOLS)['ready'])
        s=state();s.pop('routingCapabilities')
        self.assertFalse(b.readiness(s,TOOLS)['ready'])
        self.assertFalse(b.readiness(state(),[])['ready'])

    def test_request_ids_bind_full_plan_and_prompt(self):
        c=corpus();first=b.make_plan(c,state())
        second=b.make_plan(c,state())
        self.assertEqual(first,second)
        c['packets'][0]['prompt']+='new question'
        c['packets'][0]['promptSha256']=b.sha(c['packets'][0]['prompt'])
        self.assertNotEqual(first['units'][0]['id'],b.make_plan(c,state())['units'][0]['id'])

    def test_strict_json_rejects_fences_bool_integer_and_capping(self):
        packet=corpus()['packets'][0]
        self.assertEqual(b.grade(packet,'{"missing":null}')['quality'],1)
        self.assertEqual(b.grade(packet,'```json\n{"missing":null}\n```')['quality'],0)
        packet['evaluator']['expected']={'value':True}
        self.assertEqual(b.grade(packet,'{"value":1}')['quality'],0)
        self.assertEqual(b.grade(packet,'{"value":true}',truncated=True)['quality'],0)

    def test_expression_is_compared_without_execution(self):
        p=corpus()['packets'][0];p['evaluator']=dict(kind='expression_json',expected={'replacement':'len(data) <= MAX_FILE'})
        self.assertEqual(b.grade(p,'{"replacement":"len(data)<=MAX_FILE"}')['quality'],1)
        self.assertEqual(b.grade(p,'{"replacement":"__import__(\"os\").remove(\"anything\")"}')['quality'],0)

    def test_human_grade_requires_actual_bound_review(self):
        p=corpus()['packets'][0];p['evaluator']={'kind':'human_rubric'}
        self.assertEqual(b.grade(p,'An answer')['status'],'human_review_pending')
        with self.assertRaises(ValueError): b.grade(p,'An answer',judgment={'quality':1})
        judgment=dict(promptSha256=p['promptSha256'],answerSha256=b.sha('An answer'),quality=.8,
                      criticalPassed=True,reviewedBy='actual human fixture',reviewedAt=b.utc(),notes='Criterion 4 absent')
        self.assertEqual(b.grade(p,'An answer',judgment=judgment)['quality'],.8)

    def test_classifier_grade_all_three_labels(self):
        p=corpus()['packets'][0]
        value={k:p[k] for k in ('task','complexity','responseFormat')}
        self.assertEqual(b.grade_classification(p,{'classification':value})['quality'],1)
        value['complexity']='complex'
        graded=b.grade_classification(p,{'classification':value})
        self.assertEqual(graded['quality'],1)
        self.assertFalse(graded['exactLabelsPassed'])

    def test_budget_charge_idempotency_and_immutable_plan(self):
        p=b.make_plan(corpus(),state());u=p['units'][0]
        with tempfile.TemporaryDirectory() as tmp:
            l=b.Ledger(tmp,p,clock=lambda:1000);l.begin();l.reserve(u);l.reserve(u)
            self.assertEqual(l.value['chargedCalls'],1)
            l.value['chargedCalls']=600
            with self.assertRaises(b.Hold): l.reserve(p['units'][1])
            l.end()
            other=copy.deepcopy(p);other['fixtureVersion']='different'
            with self.assertRaises(b.Hold): b.Ledger(tmp,other)

    def test_active_time_excludes_normal_pause_but_conserves_crash(self):
        p=b.make_plan(corpus(),state());clock=[1000]
        with tempfile.TemporaryDirectory() as tmp:
            l=b.Ledger(tmp,p,clock=lambda:clock[0]);l.begin();clock[0]+=25;l.end()
            clock[0]+=99999
            l=b.Ledger(tmp,p,clock=lambda:clock[0]);l.begin()
            self.assertEqual(l.elapsed(),25)
            clock[0]+=21601
            with self.assertRaises(b.Hold): b.Ledger(tmp,p,clock=lambda:clock[0]).begin()

    def test_resource_receipt_must_be_fresh_exact_and_bounded(self):
        s=state();r=resources(s)
        self.assertEqual(b.resource_check(r,s,1001),{'local','msi'})
        with self.assertRaises(b.Hold): b.resource_check(r,s,1061)
        r['otherClientsIdle']=False
        with self.assertRaises(b.Hold): b.resource_check(r,s,1001)

    def test_old_installation_does_not_dispatch(self):
        c=corpus();s=state();p=b.make_plan(c,s);s.pop('routingCapabilities')
        bridge=FakeBridge(s)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(b.Hold): b.run(tmp,c,p,bridge,'manual',Path(tmp)/'absent.json')
        self.assertFalse(any(n=='submit_analysis' for n,_ in bridge.calls))

    def test_ack_loss_cannot_resubmit_on_resume(self):
        c=corpus();s=state();p=b.make_plan(c,s);p['units']=p['units'][:1]
        bridge=FakeBridge(s,fail_submit=True)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'resource.json';b.atomic(path,resources(s))
            with self.assertRaises(b.Hold): b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            bridge.fail_submit=False
            with self.assertRaises(b.Hold): b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            self.assertEqual(sum(n=='submit_analysis' for n,_ in bridge.calls),1)

    def test_retained_result_not_generated_twice_and_no_promotion(self):
        c=corpus();s=state();p=b.make_plan(c,s);p['units']=p['units'][:1]
        bridge=FakeBridge(s)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'resource.json';b.atomic(path,resources(s))
            result=b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            self.assertEqual(result['savedAttempts'],1)
            b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            self.assertEqual(sum(n=='submit_analysis' for n,_ in bridge.calls),1)
            exported=b.validated_evidence(c,p,result)
            self.assertFalse(exported['profiles'])
            self.assertEqual(exported['held'][0]['reason'],'missing_or_mismatched_effective_fingerprint')

    def evidence_fixture(self, phase='manual'):
        c=corpus();s=state();s['hosts'][0]['inferencePolicySha256']=None
        p=b.make_plan(c,s)
        first=next(u for u in p['units'] if u['phase']==phase)
        p['units']=[u for u in p['units'] if u['phase']==phase and u['packetId']==first['packetId'] and
                    u['expectedSelection']==first['expectedSelection']]
        rows=[]
        for u in p['units']:
            rows.append(dict(id=u['id'],argumentsSha256=u['argumentsSha256'],packetId=u['packetId'],phase=phase,
                result=dict(provenance=dict(model=dict(host=u['expectedSelection']['host'],id=u['expectedSelection']['model'],digest=u['expectedSelection']['digest']),
                    runtimeVersion='fixture-runtime',effectiveSettings=dict(value=s['settings']),inferencePolicySha256=None)),
                grading=dict(status='graded',quality=1,criticalPassed=True),residencyClaim='observed_loaded',seconds=2))
        return c,p,dict(corpusSha256=b.sha(c),attempts=rows)

    def test_export_explicit_null_policy_is_valid_but_missing_policy_is_held(self):
        c,p,e=self.evidence_fixture()
        out=b.validated_evidence(c,p,e)
        self.assertEqual(len(out['profiles']),1)
        self.assertIsNone(out['profiles'][0]['policySha256'])
        self.assertFalse(out['installedAcceptance'])
        for r in e['attempts']: del r['result']['provenance']['inferencePolicySha256']
        out=b.validated_evidence(c,p,e)
        self.assertFalse(out['profiles'])
        self.assertEqual(out['held'][0]['reason'],'missing_or_mismatched_effective_fingerprint')

    def test_same_digest_aliases_export_separate_profiles_and_classifier_groups(self):
        c,p,e=self.evidence_fixture()
        extra_units=[]; extra_rows=[]
        for unit,row in zip(p['units'],e['attempts']):
            other=copy.deepcopy(unit); other['id']=b.sha(dict(original=unit['id'],alias=True))[:32]
            other['expectedSelection']['model']='alias'
            extra_units.append(other)
            receipt=copy.deepcopy(row); receipt['id']=other['id']
            receipt['result']['provenance']['model']['id']='alias'
            extra_rows.append(receipt)
        p['units']+=extra_units; e['attempts']+=extra_rows
        exported=b.validated_evidence(c,p,e)
        self.assertEqual(len(exported['profiles']),2)
        self.assertEqual({r['modelTag'] for r in exported['profiles']},{'alias',p['units'][0]['expectedSelection']['model']})
        classifier_rows=[]
        for tag in ('original','alias'):
            classifier_rows.append(dict(phase='classifier',result=dict(provenance=dict(model=dict(host='local',id=tag,digest='a'*64))),grading={}))
        self.assertEqual(len(b.classifier_accuracy(classifier_rows)),2)

    def test_classifier_export_has_fixed_profile_and_response_budget(self):
        c,p,e=self.evidence_fixture('classifier')
        out=b.validated_evidence(c,p,e)
        self.assertEqual(len(out['classifiers']),1)
        self.assertEqual(out['classifiers'][0]['profile'],'routing_v1')
        self.assertEqual(out['classifiers'][0]['responseTokens'],512)
        self.assertNotIn('task',out['classifiers'][0])

    def test_export_rejects_wrong_runtime_and_resumed_latency(self):
        c,p,e=self.evidence_fixture()
        for r in e['attempts']: r['result']['provenance']['runtimeVersion']='changed'
        self.assertFalse(b.validated_evidence(c,p,e)['profiles'])
        c,p,e=self.evidence_fixture()
        for r in e['attempts']: r['resumed']=True
        self.assertEqual(b.validated_evidence(c,p,e)['held'][0]['reason'],'no_observed_warm_correct_sample')

    def test_mode_latency_only_uses_same_correct_holdouts(self):
        c=corpus();packet=next(p for p in c['packets'] if p['split']=='holdout');rows=[]
        for mode,seconds in zip(b.MODES,(3,2,4)):
            rows.append(dict(phase='replay',mode=mode,packetId=packet['id'],repetition=1,
                             completionStatus='completed',grading=dict(quality=1,criticalPassed=True),seconds=seconds))
        comparison=b.matched_mode_comparison(rows,c)
        self.assertEqual(comparison['modes']['fast']['matchedCompletionMedianSeconds'],2)
        rows[0]['grading']['quality']=0
        self.assertIsNone(b.matched_mode_comparison(rows,c)['modes']['fast']['matchedCompletionMedianSeconds'])

    def test_heldout_manual_oracle_cannot_change_role_profile(self):
        c,p,e=self.evidence_fixture()
        development=b.validated_evidence(c,p,e)['profiles'][0]
        held=next(packet for packet in c['packets'] if packet['split']=='holdout')
        first=p['units'][0]
        unit=dict(first,id='holdout-unit',packetId=held['id'],argumentsSha256='heldout-hash')
        p['units'].append(unit)
        row=copy.deepcopy(e['attempts'][0]);row.update(id=unit['id'],packetId=held['id'],argumentsSha256=unit['argumentsSha256'])
        row['grading']=dict(status='graded',quality=0,criticalPassed=False)
        e['attempts'].append(row)
        self.assertEqual(b.validated_evidence(c,p,e)['profiles'][0],development)
        e['attempts']=[row]
        self.assertFalse(b.validated_evidence(c,p,e)['profiles'])

    def test_allowlisted_repair_fixtures_execute_only_trusted_operations(self):
        self.assertTrue(b.bounded_repair_fixture('utf8_guard','len(data)<=MAX_FILE')['passed'])
        self.assertFalse(b.bounded_repair_fixture('utf8_guard','len(content)<=MAX_FILE')['passed'])
        self.assertTrue(b.bounded_repair_fixture('mw5_rate','count ? correct / count : null')['passed'])
        self.assertFalse(b.bounded_repair_fixture('mw5_rate','count ? correct / count : 0')['passed'])
        self.assertTrue(b.bounded_repair_fixture('stats_missing','float(numeric.sum()) if complete else None')['passed'])
        self.assertFalse(b.bounded_repair_fixture('stats_missing','float(numeric.sum()) if complete else 0')['passed'])
        self.assertFalse(b.bounded_repair_fixture('utf8_guard',"__import__('os').remove('anything')")['passed'])
        self.assertFalse(b.bounded_repair_fixture('utf8_guard','len(data)<=MAX_FILE')['arbitraryCodeExecuted'])

    def test_budget_mirrors_actual_bridge_and_classifier_literals(self):
        app=Path(__file__).resolve().parent
        bridge_literals=[n.value for n in b.ast.walk(b.ast.parse((app/'interlinked_ai_bridge.py').read_text(encoding='utf-8-sig')))
                         if isinstance(n,b.ast.Constant) and isinstance(n.value,str)]
        ai_source=(app/'interlinked_ai.py').read_text(encoding='utf-8-sig')
        ai_literals=[n.value for n in b.ast.walk(b.ast.parse(ai_source)) if isinstance(n,b.ast.Constant) and isinstance(n.value,str)]
        self.assertIn(b.BRIDGE_ANALYSIS_SYSTEM,bridge_literals)
        self.assertIn(b.CLASSIFIER_SYSTEM,ai_literals)
        self.assertIn('max(512, context_tokens - response_tokens - 512)',ai_source)
        prompt='é "quoted" \\ reference'
        settings={'contextTokens':8192,'responseTokens':6144}
        value=b.context_budget(prompt,settings,'replay')
        expected=len((b.BRIDGE_ANALYSIS_SYSTEM+'[]\n'+prompt+'[]').encode('utf-8'))
        self.assertEqual(value['answer']['assembledBytes'],expected)
        self.assertEqual(value['answer']['limitBytes'],1536)
        self.assertEqual(value['classifier']['responseTokens'],512)

    def test_budget_is_phase_aware_and_never_silently_truncates(self):
        settings={'contextTokens':8192,'responseTokens':6144}
        self.assertTrue(b.context_budget('x'*1100,settings,'manual')['fits'])
        self.assertFalse(b.context_budget('x'*2000,settings,'manual')['fits'])
        self.assertTrue(b.context_budget('x'*2000,settings,'classifier')['fits'])
        classified=b.context_budget('x'*5000,settings,'classifier')
        self.assertFalse(classified['fits'])
        self.assertTrue(classified['classifier']['wouldTruncate'])
        self.assertFalse(b.context_budget('é'*1000,settings,'manual')['fits'])

    def test_oversized_case_is_unsubmitted_uncharged_and_not_quality_failure(self):
        c=corpus();c['packets'][0]['prompt']+='x'*2000;c['packets'][0]['promptSha256']=b.sha(c['packets'][0]['prompt'])
        s=state();s['settings']['responseTokens']=6144;p=b.make_plan(c,s);p['units']=p['units'][:1]
        bridge=FakeBridge(s)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'resource.json';b.atomic(path,resources(s))
            result=b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            self.assertEqual(result['chargedCallCeiling'],0)
            self.assertEqual(result['savedAttempts'],0)
            self.assertEqual(result['contextBudgetSummary']['heldUnits'],1)
            self.assertFalse(result['contextBudgetSummary']['admissionHoldsAreQualityFailures'])
            self.assertFalse(any(name=='submit_analysis' for name,_ in bridge.calls))

    def test_actual_model_failure_remains_quality_failure_after_admitted_case(self):
        class FailedBridge(FakeBridge):
            def call(self,name,args):
                if name=='get_job':
                    self.calls.append((name,args));return dict(id='fixture-job',status='failed',errorCode='empty_analysis_response')
                return super().call(name,args)
        c=corpus();s=state();p=b.make_plan(c,s);p['units']=p['units'][:1];bridge=FailedBridge(s)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'resource.json';b.atomic(path,resources(s))
            result=b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
            self.assertEqual(result['chargedCallCeiling'],1)
            self.assertEqual(result['attempts'][0]['grading']['quality'],0)
            self.assertEqual(result['attempts'][0]['grading']['status'],'failed')

    def test_duplicate_phase_or_packet_receipts_cannot_promote_evidence(self):
        c,p,e=self.evidence_fixture()
        e['attempts']=[e['attempts'][0],copy.deepcopy(e['attempts'][0])]
        with self.assertRaises(ValueError): b.validated_evidence(c,p,e)
        c,p,e=self.evidence_fixture();e['attempts'][0]['phase']='classifier'
        with self.assertRaises(ValueError): b.validated_evidence(c,p,e)
        c,p,e=self.evidence_fixture();e['attempts'][0]['packetId']=c['packets'][1]['id']
        with self.assertRaises(ValueError): b.validated_evidence(c,p,e)

    def test_active_gui_buddy_or_worker_inference_yields_before_charge(self):
        c=corpus();s=state();p=b.make_plan(c,s);p['units']=p['units'][:1]
        for origin in ('assistant','buddy','worker'):
            busy=copy.deepcopy(s);busy['activity']['activeCalls']=[{'origin':origin}]
            bridge=FakeBridge(busy)
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'resource.json';b.atomic(path,resources(s))
                with self.assertRaises(b.Hold): b.run(tmp,c,p,bridge,'manual',path,clock=lambda:1000)
                self.assertFalse(any(name=='submit_analysis' for name,_ in bridge.calls))
                self.assertEqual(b.read_json(Path(tmp)/'ledger.json')['chargedCalls'],0)
        unknown=copy.deepcopy(s);unknown['activity']['partial']=True
        with self.assertRaises(b.Hold): b.require_coordinator_idle(unknown)

    def test_complete_negative_evidence_is_retained_without_warm_latency(self):
        c,p,e=self.evidence_fixture()
        for row in e['attempts']:
            row['grading'].update(quality=0,criticalPassed=False)
            row['residencyClaim']='unknown'
        out=b.validated_evidence(c,p,e)
        self.assertFalse(out['profiles'])
        self.assertEqual(len(out['rejectedProfiles']),1)
        negative=out['rejectedProfiles'][0]
        self.assertEqual(negative['quality'],0)
        self.assertFalse(negative['criticalPassed'])
        self.assertNotIn('warmSeconds',negative)
        incomplete=copy.deepcopy(e);incomplete['attempts'].pop()
        self.assertFalse(b.validated_evidence(c,p,incomplete)['rejectedProfiles'])
        pending=copy.deepcopy(e);pending['attempts'][0]['grading']['quality']=None
        self.assertFalse(b.validated_evidence(c,p,pending)['rejectedProfiles'])
        changed=copy.deepcopy(e)
        for row in changed['attempts']:row['result']['provenance']['runtimeVersion']='wrong-runtime'
        self.assertFalse(b.validated_evidence(c,p,changed)['rejectedProfiles'])


if __name__=='__main__':
    unittest.main()
