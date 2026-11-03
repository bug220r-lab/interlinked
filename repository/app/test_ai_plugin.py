"""MCP facade contract and real stdio discovery without a coordinator."""
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_bridge as bridge


class Stub:
    def __init__(self): self.calls = []
    def request(self, endpoint, action, body):
        self.calls.append((action,body))
        return {'id':'a'*32,'status':'queued'}


class PluginTests(unittest.TestCase):
    def setUp(self): self.ipc = Stub()
    def call(self,name,body):
        return bridge.handle(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name=name,arguments=body)),self.ipc,Path('unused'))

    def test_uncorrelated_calls_and_invalid_ids_never_dispatch(self):
        for identifier in (None,True,{},[], '', 'x'*129):
            message=dict(jsonrpc='2.0',method='tools/call',id=identifier,params=dict(name='refresh_models',arguments={'requestId':'a'*32}))
            with self.assertRaises(ValueError): bridge.handle(message,self.ipc,Path('unused'))
        self.assertEqual(self.ipc.calls,[])

    def test_exact_tool_contract(self):
        expected={'get_ai_state','search_context','submit_analysis','classify_prompt','start_worker','refresh_models','calibrate_model','load_model','request_unload','update_ai_settings','get_job','get_result','cancel_job'}
        self.assertEqual(set(bridge.SCHEMAS),expected)
        self.assertTrue(all(not t['inputSchema']['additionalProperties'] for t in bridge.TOOLS))
        self.assertFalse(self.call('submit_analysis',{'requestId':'a'*32,'text':'Summarize','contextScope':'supplied_text_only'})['isError'])
        self.assertEqual(len(self.ipc.calls),1)

    def test_forbidden_actions_and_extra_authority_never_dispatched(self):
        cases=[('tasks/apply',{'id':'a'*32}),('request_unload',{'requestId':'a'*32,'host':'local','model':'test','confirmedExternalIdle':True}),
               ('get_ai_state',{'refresh':True}),('start_worker',{'requestId':'a'*32,'projectId':'p','prompt':'x','command':'whoami'}),
               ('calibrate_model',{'requestId':'a'*32,'host':'local','model':'test','capability':'vision','path':'C:/secret.png'})]
        for name,body in cases: self.assertTrue(self.call(name,body)['isError'])
        self.assertEqual(self.ipc.calls,[])

    def test_invalid_identity_types_budgets_and_scope_never_dispatched(self):
        cases=[('submit_analysis',{'requestId':'bad','text':'x'}),('submit_analysis',{'requestId':'a'*32,'text':'x','contextScope':'all_files'}),
               ('start_worker',{'requestId':'a'*32,'projectId':'p','prompt':'x','maxSteps':True}),
               ('start_worker',{'requestId':'a'*32,'projectId':'p','prompt':'x','timeoutSeconds':100000}),
               ('search_context',{'query':'x','projectIds':['p','p']}),('get_job',{'id':'A'*32})]
        for name,body in cases: self.assertTrue(self.call(name,body)['isError'])
        self.assertEqual(self.ipc.calls,[])

    def test_settings_require_revision_and_full_bounded_object(self):
        value=dict(disabledModels=[],contextTokens=8192,responseTokens=2048,requestSeconds=180,queueSeconds=600,speakReplies=False)
        args=dict(requestId='a'*32,settingsRevision='revision',value=value)
        self.assertFalse(self.call('update_ai_settings',args)['isError'])
        value['contextTokens']=100000
        self.assertTrue(self.call('update_ai_settings',args)['isError'])
        self.assertEqual(len(self.ipc.calls),1)

    def test_stdio_discovery_does_not_require_or_start_coordinator(self):
        with tempfile.TemporaryDirectory() as temp:
            endpoint=Path(temp)/'missing'/'endpoint.json'
            messages=[dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2025-06-18'}),
                      dict(jsonrpc='2.0',method='notifications/initialized'),dict(jsonrpc='2.0',id=2,method='tools/list'),
                      dict(jsonrpc='2.0',id=3,method='tools/call',params={'name':'get_ai_state','arguments':{}})]
            done=subprocess.run([sys.executable,'-I','-B',str(Path(bridge.__file__)),'--endpoint',str(endpoint)],
                input=''.join(json.dumps(m)+'\n' for m in messages),text=True,capture_output=True,timeout=15)
            self.assertEqual(done.returncode,0,done.stderr)
            replies=[json.loads(line) for line in done.stdout.splitlines()]
            self.assertEqual([r['id'] for r in replies],[1,2,3])
            self.assertEqual(len(replies[1]['result']['tools']),13)
            self.assertTrue(replies[2]['result']['isError'])
            self.assertFalse(endpoint.parent.exists())
            self.assertNotIn(str(endpoint),done.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows plugin package required')
    def test_generated_package_pins_installed_runtime_bridge_and_endpoint(self):
        base=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'package'
            powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result=subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(base/'repository/ai-plugin/Build-AIPlugin.ps1'),'-OutputDirectory',str(output)],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((output/'.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))
            self.assertTrue(manifest['requires_local_executor'])
            self.assertEqual(manifest['name'],'interlinked-ai')
            config=json.loads((output/'.mcp.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(set(config['mcpServers']),{'interlinked_ai'})
            entry=config['mcpServers']['interlinked_ai']
            kit=Path('C:/Users/Jude/Documents/MSI-Project-Library')
            self.assertEqual(Path(entry['command']),kit/'Project-Library-Client/Runtime/python.exe')
            self.assertEqual(entry['args'][:2],['-I','-B'])
            self.assertEqual(Path(entry['args'][2]),kit/'INTERLINKED-App/ai_bridge.py')
            self.assertEqual(entry['args'][3],'--endpoint')
            self.assertEqual(Path(entry['args'][4]),kit/'INTERLINKED-GUI-State/ai/bridge/endpoint.json')
            checks=json.loads((output/'PLUGIN-CHECKSUMS.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(set(checks['files']),{'.codex-plugin/plugin.json','.mcp.json','skills/local-ai/SKILL.md'})
            for name,expected in checks['files'].items():
                self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(),expected)


@unittest.skipUnless(os.name == 'nt', 'Windows named-pipe integration required')
class IntegratedPluginTests(unittest.TestCase):
    """Actual portable-interpreter MCP -> pipe -> coordinator; synthetic models."""
    def setUp(self):
        from test_ai_bridge import BridgeTests
        import interlinked_ai_ipc as ipc
        self.fixture = BridgeTests(methodName='runTest')
        self.fixture.setUp()
        self.endpoint = self.fixture.bridge.root / 'endpoint.json'
        self.server = ipc.PipeServer(self.endpoint, self.fixture.bridge.dispatch).start()
        portable = Path('C:/Users/Jude/Documents/MSI-Project-Library/Project-Library-Client/Runtime/python.exe')
        self.assertTrue(portable.is_file(), 'Reviewed installed portable Python required for this fixture.')
        self.process = subprocess.Popen([str(portable),'-I','-B',str(Path(bridge.__file__)),'--endpoint',str(self.endpoint)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        self.replies = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.replies.put(json.loads(line))
        self.reader = threading.Thread(target=read,daemon=True); self.reader.start()
        self.sequence = 0

    def tearDown(self):
        self.process.stdin.close()
        try: self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=3)
        self.reader.join(timeout=3)
        self.process.stdout.close(); self.process.stderr.close()
        self.server.close()
        self.fixture.tearDown()

    def rpc(self,method,params=None):
        self.sequence += 1
        message=dict(jsonrpc='2.0',id=self.sequence,method=method,params=params or {})
        self.process.stdin.write(json.dumps(message)+'\n'); self.process.stdin.flush()
        reply=self.replies.get(timeout=10)
        self.assertEqual(reply['id'],self.sequence)
        return reply['result']

    def tool(self,name,body):
        result=self.rpc('tools/call',dict(name=name,arguments=body))
        self.assertFalse(result['isError'], result)
        return json.loads(result['content'][0]['text'])

    def wait_job(self,identifier):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            row=self.tool('get_result',dict(id=identifier))
            if row['status'] not in ('queued','running','cancelling'): return row
            time.sleep(.02)
        self.fail('Fixture job did not finish.')

    def test_discover_submit_poll_result_and_authority_boundaries(self):
        self.assertEqual(self.rpc('initialize')['serverInfo']['name'],'interlinked-ai')
        self.assertEqual(len(self.rpc('tools/list')['tools']),13)
        state=self.tool('get_ai_state',{})
        self.assertEqual({p['id'] for p in state['projects']},{'fixture','other'})
        self.assertNotIn(str(self.fixture.root),json.dumps(state))
        body=dict(requestId=uuid.uuid4().hex,text='Summarize Lantern.',host='local',model='writer',contextScope='supplied_text_only')
        count=len(self.fixture.ai.nodes['local'].calls)
        first=self.tool('submit_analysis',body)
        second=self.tool('submit_analysis',body)
        self.assertEqual(first['id'],second['id'])
        status=self.tool('get_job',dict(id=first['id']))
        self.assertIn(status['status'],('queued','running','completed'))
        result=self.wait_job(first['id'])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['result']['sources'],[])
        self.assertEqual(result['provenance']['model']['digest'],'a'*64)
        self.assertEqual(len(self.fixture.ai.nodes['local'].calls),count+1)
        for name,args in [('tasks/apply',dict(id=first['id'])),
                          ('get_result',dict(id='f'*32)),
                          ('submit_analysis',dict(body,text='x'*24001)),
                          ('request_unload',dict(requestId=uuid.uuid4().hex,host='local',model='writer',confirmedExternalIdle=True))]:
            self.assertTrue(self.rpc('tools/call',dict(name=name,arguments=args))['isError'])

    def test_cancel_cross_process_request_retains_evidence(self):
        self.fixture.ai.nodes['local'].hold=threading.Event()
        first=self.tool('submit_analysis',dict(requestId=uuid.uuid4().hex,text='Bounded cancel fixture.',contextScope='supplied_text_only',host='local',model='writer'))
        self.tool('cancel_job',dict(id=first['id']))
        result=self.wait_job(first['id'])
        self.assertEqual(result['status'],'cancelled',result)
        self.assertIn('provenance',result)


if __name__ == '__main__': unittest.main()
