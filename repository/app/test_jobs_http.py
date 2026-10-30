"""Authenticated loopback MSI jobs API; synthetic state only, no SSH or jobs."""
from pathlib import Path
import argparse
import http.client
import importlib.util
import json
import sys
import threading
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.absolute()/('jobs-http-'+uuid.uuid4().hex)
    root.mkdir(parents=True)
    source=Path(__file__).resolve().with_name('interlinked_gui.py')
    spec=importlib.util.spec_from_file_location('jobs_http_gui',source)
    g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
    runtime=g.demo_runtime(root/'state')
    controller=g.load_companion('interlinked_controller').controller_class(g)(runtime)
    server=None;checks=[]

    def check(value,label):
        if not value:raise AssertionError(label)
        checks.append(label)

    def until(predicate,label):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.03)
        raise AssertionError(label)

    try:
        server=g.Server(controller,token='jobs-fixture-token')
        threading.Thread(target=server.serve_forever,daemon=True).start()

        def request(method,path,body=None,headers=None,token=True):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            supplied={'X-INTERLINKED-Token':server.token} if token else {}
            data=None if body is None else json.dumps(body)
            if data is not None:supplied.update({'Content-Type':'application/json','Origin':server.origin})
            supplied.update(headers or {})
            try:
                connection.request(method,path,data,supplied)
                response=connection.getresponse()
                return response.status,dict(response.getheaders()),response.read()
            finally:connection.close()

        status,headers,data=request('GET','/api/msi/jobs')
        snapshot=json.loads(data)
        check(request('GET','/?view=jobs',token=False)[0]==200,'The Jobs navigation entry can load only the public static shell')
        check(request('GET','/?view=jobs&path=elsewhere')[0]==403,'Jobs navigation cannot supply arbitrary queries')
        check(status==200 and snapshot['readOnly'] and snapshot['demo'],'Jobs snapshot is explicitly read-only and synthetic')
        check(headers['Cache-Control']=='no-store','Private job observations are not HTTP cached')
        check(not snapshot['refreshing'],'Reading the jobs panel never initiates SSH')
        log_path='/api/msi/jobs/log?job='+'a'*32+'&stream=stdout'
        for path in ('/api/msi/jobs',log_path):
            check(request('GET',path,token=False)[0]==403,path+' requires capability token')
            check(request('GET',path,headers={'Host':'foreign.invalid'})[0]==403,path+' rejects foreign Host')
            check(request('GET',path,headers={'Origin':'https://foreign.invalid'})[0]==403,path+' rejects foreign Origin')
            check(request('GET',path,headers={'Sec-Fetch-Site':'cross-site'})[0]==403,path+' rejects cross-site source')
        check(request('GET','/api/msi/jobs?token='+server.token)[0]==404,'Capabilities cannot be supplied through query strings')
        for query in ('job=../../x&stream=stdout','job='+'a'*32+'&stream=../../stderr',
                      'job='+'a'*32+'&stream=stdout&stream=stderr','job='+'a'*32+'&stream=stdout&path=elsewhere',
                      'job='+'a'*32):
            check(request('GET','/api/msi/jobs/log?'+query)[0]==403,'Malformed log selection is refused: '+query)
        for body in ({'command':'whoami'},{'jobId':'a'*32},{'host':'elsewhere'},{'path':'elsewhere'}):
            check(request('POST','/api/msi/jobs/refresh',body)[0]==409,'Refresh refuses caller-controlled command, job, host or path')
        for path in ('/api/msi/jobs/start','/api/msi/jobs/cancel','/api/msi/jobs/delete','/api/msi/jobs/import'):
            check(request('POST',path,{})[0]==409,'No remote mutation endpoint exists: '+path)
        check(request('POST','/api/msi/jobs/refresh',{},token=False)[0]==409,'Refresh requires capability token')
        check(request('POST','/api/msi/jobs/refresh',{},headers={'Origin':'https://foreign.invalid'})[0]==409,'Refresh rejects foreign Origin')
        check(request('POST','/api/msi/jobs/refresh',{},headers={'Origin':''})[0]==409,'Refresh requires exact same Origin')

        # The independent observer stays available during an actual synthetic
        # editing session; no real editor or cache operation is launched.
        project=runtime.index['projects'][0]
        controller.start('open',project['id'],project['entryPoints'][0]['path'])
        until(lambda:controller.state()['operation'].get('awaitingDone'),'Synthetic editor session did not reach its prompt')
        operation=controller.state()['operation']['id']
        check(request('POST','/api/msi/jobs/refresh',{})[0]==200,'Read-only MSI refresh is available during a local editing session')
        snapshot=until(lambda:(lambda v:v if not v['refreshing'] and v['jobs'] else None)(json.loads(request('GET','/api/msi/jobs')[2])),'Synthetic job refresh did not finish')
        check(controller.state()['operation']['id']==operation and controller.state()['operation']['awaitingDone'],'Job observation preserves the local editing operation and completion prompt')
        job=snapshot['jobs'][0]
        for stream in ('stdout','stderr'):
            status,headers,data=request('GET','/api/msi/jobs/log?job='+job['jobId']+'&stream='+stream)
            log=json.loads(data)
            check(status==200 and log['readOnly'] and log['stream']==stream,'Only the selected cached '+stream+' tail is returned')
            check(log['observedAt']==snapshot['observedAt'],'Log tail and status share their observation timestamp')
        check(request('GET','/api/msi/jobs/log?job='+'f'*32+'&stream=stdout')[0]==403,'Unknown jobs cannot select other files')
        check(request('GET','/api/catalog')[0]==200,'Project catalog remains available during job inspection')
        active=controller.state()['operation']
        controller.session_input('DONE',active['id'],active['promptGeneration'])
        until(lambda:not controller.busy(),'Synthetic editing session did not finish')
        check(controller.state()['canQuit'],'MSI observations never hold the local editing or quit lock')
        result=dict(status='PASS',count=len(checks),checks=checks,scope='Synthetic demo state and authenticated loopback API only; no MSI access, credentials, production installation or jobs.')
        (root/'results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(status='PASS',checks=len(checks),receipt=str(root/'results.json'))))
    finally:
        if controller.busy():
            controller.demo_done.set()
            until(lambda:not controller.busy(),'Synthetic session cleanup did not finish')
        if server is not None:server.shutdown();server.server_close()
        controller.close()


if __name__=='__main__':main()
