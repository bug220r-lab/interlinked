"""Small local-only fixtures for the prepared new-PC verification wrapper."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import prepare_fixture as prep
run=prep.runner
CLIENT=HERE.parents[1]/'outputs/Project-Library-2026-10-29/Client'
results=[]

def case(name, operation):
    operation();results.append({'name':name,'status':'PASS'})

with tempfile.TemporaryDirectory(prefix='local-fixtures-',dir=HERE) as temp:
    root=Path(temp)
    def fixture(name):
        return prep.build(root/name)
    def roundtrip():
        plan,library,returns=fixture('roundtrip')
        result=run.run(plan,CLIENT,library.parent,local_test_roots=(library,returns))
        assert result['status']=='PASS',result
        assert len(result['steps'])==7 and result['finalSyntheticWorkspaceEmpty']
        assert result['savedVersionRefetchBytesVerified'] and result['sourceHashesUnchanged']
        assert result['mode']=='LOCAL_FIXTURE_ONLY'
        repeated=run.run(plan,CLIENT,library.parent,local_test_roots=(library,returns))
        assert repeated['status']=='INCOMPLETE' and 'prior/unexpected state' in repeated['error']
    case('complete seven-step local roundtrip and reuse refusal',roundtrip)
    def corrupt():
        plan,library,returns=fixture('corrupt')
        (library/'Sources'/run.PREFIX/'edit-me.txt').write_bytes(b'changed')
        result=run.run(plan,CLIENT,library.parent,local_test_roots=(library,returns))
        assert result['status']=='INCOMPLETE' and 'source changed' in result['error']
        assert not (returns/run.PROJECT).exists()
    case('corrupt source blocks before client operations',corrupt)
    def foreign():
        plan,library,returns=fixture('foreign')
        protected=returns/'user-notes.txt';protected.write_bytes(b'preserve this')
        result=run.run(plan,CLIENT,library.parent,local_test_roots=(library,returns))
        assert result['status']=='INCOMPLETE' and protected.read_bytes()==b'preserve this'
    case('unexpected Returns file preserved',foreign)
    def bad_paths():
        plan,library,returns=fixture('bad-path')
        plan['fixtureName']='../production'
        try:run.validate_plan(plan)
        except RuntimeError:pass
        else:raise AssertionError('unsafe fixture accepted')
    case('fixture traversal identifier rejected',bad_paths)
    def bad_catalog():
        plan,library,returns=fixture('bad-catalog')
        data=json.loads((library/'catalog.json').read_text('utf-8'))
        data['projects'][0]['files'][0]['sourceRelative']='Sources/../outside'
        content=json.dumps(data).encode();(library/'catalog.json').write_bytes(content)
        plan['catalogSha256']=run.sha(content)
        result=run.run(plan,CLIENT,library.parent,local_test_roots=(library,returns))
        assert result['status']=='INCOMPLETE' and 'metadata differs' in result['error']
    case('catalog path redirection rejected even with matching catalog digest',bad_catalog)
    def unc_receipt_guard():
        with patch.object(run,'plain',return_value=Path(r'\\server\share\kit')), patch.object(run,'write_json_new') as writer:
            assert run.main()==2
            writer.assert_not_called()
    case('UNC invocation cannot write a failure receipt remotely',unc_receipt_guard)
    def late_failure_receipt():
        kit=root/'late-report';kit.mkdir();(kit/'New-PC-Verification-Plan.json').write_text('{}')
        with patch.object(run,'__file__',str(kit/'runner.py')), patch.object(run,'run',side_effect=OSError('final report write failed; retained evidence path')):
            assert run.main()==2
        receipts=list(kit.glob('Verification-Failure-*.json'));assert len(receipts)==1
        report=json.loads(receipts[0].read_text('utf-8'));assert report['phase']=='VERIFICATION_OR_REPORTING'
        assert report['status']!='PASS' and 'retained evidence' in report['error']
    case('late reporting failure labeled separately from preflight',late_failure_receipt)

summary={'status':'PASS','tests':results,'networkAccessed':False,'productionCacheOrSourcesUsed':False,
         'runnerSha256':run.sha((HERE/'verify_new_pc_workflow.py').read_bytes())}
(HERE/'validation-results.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
