"""Real loopback MASTER API checks using isolated demo state and preview only."""
from pathlib import Path
import argparse
import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.absolute()/('master-http-'+uuid.uuid4().hex)
    root.mkdir(parents=True)
    source=Path(__file__).resolve().with_name('interlinked_gui.py')
    spec=importlib.util.spec_from_file_location('master_http_gui',source)
    g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
    runtime=g.demo_runtime(root/'state');runtime.master_worker=args.worker.absolute()
    controller=g.load_companion('interlinked_controller').controller_class(g)(runtime)
    server=None;checks=[]

    def check(condition,label):
        if not condition:raise AssertionError(label)
        checks.append(label)

    def until(predicate,label):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.04)
        raise AssertionError(label)

    try:
        server=g.Server(controller,token='private-fixture-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None,token=True):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            supplied={'X-INTERLINKED-Token':server.token} if token else {}
            data=None if body is None else json.dumps(body)
            if data is not None:supplied.update({'Content-Type':'application/json','Origin':server.origin})
            supplied.update(headers or {})
            try:
                connection.request(method,path,data,supplied)
                response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
            finally:connection.close()

        long_directory=root/('metadata-'+('x'*70))/('level-'+('y'*55))
        os.makedirs(g.fs(long_directory))
        long_record=long_directory/('record-'+('z'*35)+'.json')
        g.write_json(long_record,{'format':'long-path-fixture-v1'})
        check(len(str(long_record))>260 and g.read_json(long_record)['format']=='long-path-fixture-v1',
              'Normal Windows paths support atomic private metadata writes beyond 260 characters')

        check(request('GET','/api/state')[0]==200,'Core project API is available before MASTER operations')
        until(lambda:request('GET','/api/master/frame')[0]==200,'Real native preview did not arrive')
        for path in ('/api/master/state','/api/master/settings','/api/master/frame'):
            check(request('GET',path,token=False)[0]==403,path+' requires capability token')
            check(request('GET',path,headers={'Host':'foreign.invalid'})[0]==403,path+' rejects foreign Host')
            check(request('GET',path,headers={'Origin':'https://foreign.invalid'})[0]==403,path+' rejects foreign Origin')
            check(request('GET',path,headers={'Sec-Fetch-Site':'cross-site'})[0]==403,path+' rejects cross-site source')
            check(request('GET',path+'?token='+server.token)[0]==404,path+' refuses query-string capability')
        status,headers,picture=request('GET','/api/master/frame')
        check(status==200 and headers['Content-Type']=='image/jpeg' and picture[:2]==b'\xff\xd8','Preview endpoint returns actual encoded native JPEG')
        check(headers['Cache-Control']=='no-store','Private preview is not cached')
        snapshot=json.loads(request('GET','/api/master/state')[2])
        check(snapshot['demo'] and len(snapshot['projects'])==3,'Demo snapshot contains three truthful default project stations')
        check(not snapshot['violet']['connected'],'No fixture or source inspection claims actual Dot connection')
        check(not snapshot['display']['connected'],'Preview alone does not connect to hardware')
        for path,body in (('/api/master/settings',{'fps':5}),('/api/master/display',{'connect':False})):
            check(request('POST',path,body,token=False)[0]==409,path+' requires capability token')
            check(request('POST',path,body,headers={'Origin':'https://foreign.invalid'})[0]==409,path+' rejects foreign write origin')
            check(request('POST',path,body,headers={'Origin':''})[0]==409,path+' requires exact write origin')
        check(request('POST','/api/master/settings',{'fps':5})[0]==200,'Valid MASTER preference changes are accepted')
        check(json.loads(request('GET','/api/master/settings')[2])['fps']==5,'MASTER settings roundtrip uses authenticated endpoint')
        for body in ({'helperPins':{'USBLCDNEW.dll':'a'*64}},{'executable':'cmd.exe'}, {'requireHelperPins':False},
                     {'projects':[{'id':'bad','label':'Bad','root':r'\\MSI\ProjectLibrary'}]},
                     {'captureEnabled':True}, {'fps':30}):
            check(request('POST','/api/master/settings',body)[0]==409,'Untrusted MASTER configuration capability is rejected: '+str(next(iter(body))))
        check(request('POST','/api/master/display',{'connect':False})[0]==200,'Display-only disconnect command is accepted without project effects')
        check(request('POST','/api/master/display',{'action':'release','project':'anything'})[0]==409,'Display route cannot dispatch project operations')
        check(request('POST','/api/master/display',{'connect':True,'executable':'bad.exe'})[0]==409,'Display route cannot launch arbitrary executables')
        check(request('POST','/api/master/display',{'action':'enable_sensors'})[0]==409,'Synthetic demo cannot trigger elevated sensor setup')
        check(json.loads(request('GET','/api/state')[2])['canQuit'],'MASTER settings do not create a project operation or held editing session')
        # Only this test-owned preview process is terminated. No USB connect was sent.
        worker=server.master._worker
        worker.kill();worker.wait(timeout=3)
        until(lambda:request('GET','/api/master/frame')[0]==503,'Exited renderer must not serve stale cached JPEGs')
        failed=json.loads(request('GET','/api/master/state')[2])
        check(not failed['display']['connected'],'Worker failure clears LCD connection claim')
        check(request('GET','/api/state')[0]==200 and request('GET','/api/catalog')[0]==200,'Worker failure leaves core project and catalog APIs available')
        # A corrupt private display configuration is isolated from library startup.
        server.shutdown();server.server_close();server=None
        m=g.load_companion('interlinked_master')
        m.write_json(runtime.state/'master/settings.json',{'format':'corrupt-fixture','settings':{}})
        server=g.Server(controller,token='second-fixture-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        check(request('GET','/api/state')[0]==200,'Core API starts with corrupt MASTER configuration')
        fallback=json.loads(request('GET','/api/master/state')[2])
        check(fallback['display']['status']=='configuration_error','Corrupt MASTER configuration is explicitly reported')
        check(request('GET','/api/master/frame')[0]==503,'Broken MASTER has no fabricated preview frame')
        check(request('POST','/api/master/settings',{'fps':10})[0]==409,'Broken MASTER preserves configuration rather than silently overwriting it')
        check(request('GET','/api/master/state',token=False)[0]==403,'Fallback state remains capability-protected')
        report=dict(format='interlinked-master-http-tests-v1',ok=True,checks=checks,count=len(checks),hardwareConnected=False,productionModified=False)
        (root/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'checks':len(checks),'receipt':str(root/'receipt.json')}))
    finally:
        if server is not None:server.shutdown();server.server_close()
        controller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
