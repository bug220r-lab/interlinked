"""Explicit, bounded picture-only acceptance on the reviewed Peerless Vision.

Requires TRCC closed. Never closes another writer or installs a driver. Reports
helper consumption separately from the user's physical-screen confirmation.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--worker',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--violet',type=Path,required=True)
    p.add_argument('--allow-usb',action='store_true')
    p.add_argument('--phase-seconds',type=int,default=60)
    p.add_argument('--soak-seconds',type=int,default=1800)
    p.add_argument('--enable-sensors',action='store_true',help='Show one Windows permission prompt for the owned sensor helper.')
    p.add_argument('--high-rates',action='store_true',help='Also measure diagnostic-only 20 and 30 FPS targets.')
    p.add_argument('--motion-confirmed',action='store_true',help='Record the user\'s already supplied physical motion confirmation.')
    a=p.parse_args()
    if not a.allow_usb:raise SystemExit('Explicit --allow-usb is required.')
    if a.output.exists():raise SystemExit('Use a new private acceptance folder.')
    if not 10<=a.phase_seconds<=120 or not 0<=a.soak_seconds<=1800:raise SystemExit('Bounded durations required.')
    a.output.mkdir(parents=True)
    vendor=Path('C:/Program Files/TRCCCAP')
    pins={name:hashlib.sha256((vendor/name).read_bytes()).hexdigest() for name in ('USBLCDNEW.exe','USBLCDNEW.dll','USBLCDNEW.runtimeconfig.json','LibUsbDotNet.LibUsbDotNet.dll','HWINFO.exe','HWiNFO64.dll')}
    pin=pins['USBLCDNEW.dll']
    proc=subprocess.Popen([str(a.worker.resolve())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    events=queue.Queue();lock=threading.Lock();latest={};frames={'count':0,'distinct':set(),'first':None,'last':None};errors=[]
    def exact(n):
        data=bytearray()
        while len(data)<n:
            block=proc.stdout.read(n-len(data))
            if not block:raise EOFError
            data.extend(block)
        return bytes(data)
    def pictures():
        try:
            while True:
                n=struct.unpack('<I',exact(4))[0]
                if not 4<n<=691136:raise ValueError('Invalid frame length')
                image=exact(n)
                with lock:
                    frames['count']+=1;frames['last']=image
                    if frames['first'] is None:frames['first']=image
                    if len(frames['distinct'])<200:frames['distinct'].add(hashlib.sha256(image).hexdigest())
        except EOFError:pass
        except Exception as ex:errors.append(str(ex))
    def diagnostics():
        with (a.output/'events.jsonl').open('wb') as log:
            for line in proc.stderr:
                if log.tell()<8*1024*1024:log.write(line);log.flush()
                try:
                    event=json.loads(line)
                    with lock:latest[event.get('type')]=event
                    events.put(event)
                except Exception as ex:errors.append(str(ex))
    threading.Thread(target=pictures,daemon=True).start();threading.Thread(target=diagnostics,daemon=True).start()
    def send(obj):proc.stdin.write((json.dumps(obj)+'\n').encode());proc.stdin.flush()
    resource_module=None
    def resource_sample():
        nonlocal resource_module
        try:
            if resource_module is None:
                source=Path(__file__).with_name('master_resource_probe.py')
                spec=importlib.util.spec_from_file_location('master_acceptance_resources',source)
                resource_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(resource_module)
            return resource_module.sample_owned(proc.pid)
        except (OSError,ValueError,ImportError,AttributeError):
            resource_module=None
            return {'status':'unavailable','reason':'Owned-process resource sampler unavailable'}
    report={'format':'interlinked-master-hardware-acceptance-v1','physicalDisplayVerified':a.motion_confirmed,'phases':[],'errors':errors,'helperSha256':pin,'helperPins':pins,'workerSha256':hashlib.sha256(a.worker.read_bytes()).hexdigest(),'sensorPermissionRequested':a.enable_sensors}
    def settings(fps):return {'fps':fps,'diagnostic':True,'helperSha256':pin,'helperPins':pins,'requireHelperPins':True,'violetAtlasPath':str(a.violet.resolve())}
    try:
        send({'op':'settings','settings':settings(5)})
        send({'op':'snapshot','snapshot':{'demo':True,'projects':[{'id':key,'label':label,'activity':{'status':activity,'source':'SYNTHETIC VIDEO TEST'},'health':{'status':'unknown','summary':'Video stability test'}} for key,label,activity in [('edit','ED-IT Buddy','working'),('mw5','MW5 MOD','waiting'),('interlinked','INTERLINKED','working')]]}})
        send({'op':'display','connect':True})
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            event=events.get(timeout=5)
            if event.get('type')=='status' and event.get('connected') is True:break
            if event.get('type')=='status' and event.get('displayStatus')=='unavailable':raise RuntimeError(event.get('error','Display unavailable'))
        else:raise RuntimeError('Display connection timeout')
        if a.enable_sensors:
            send({'op':'sensors','enable':True})
        def phase(fps,duration,name):
            send({'op':'settings','settings':settings(fps)})
            time.sleep(2)
            with lock:initial=dict(latest.get('stats',{}))
            started=time.monotonic();samples=[];resources=[];next_resource=0
            while time.monotonic()-started<duration:
                if proc.poll() is not None:raise RuntimeError('Worker exited during acceptance')
                if time.monotonic()>=next_resource:
                    resources.append(resource_sample());next_resource=time.monotonic()+5
                try:event=events.get(timeout=.5)
                except queue.Empty:continue
                if event.get('type')=='stats':samples.append(event)
                if event.get('type')=='status' and event.get('displayStatus')=='unavailable':raise RuntimeError(event.get('error','Display disconnected'))
            with lock:final=dict(latest.get('stats',{}))
            elapsed=time.monotonic()-started
            delivered=final.get('acknowledgedFrames',0)-initial.get('acknowledgedFrames',0)
            cpu_ms=final.get('cpuTotalMs',0)-initial.get('cpuTotalMs',0)
            row={'name':name,'targetFps':fps,'seconds':elapsed,'acknowledgedFrames':delivered,'deliveredFps':delivered/elapsed,'cpuCorePercent':cpu_ms/(elapsed*10),'maxMemoryBytes':max([s.get('workingSetBytes',0) for s in samples] or [0]),'initial':initial,'final':final,'samples':samples}
            with lock:
                row['sensors']=dict(latest.get('sensors',{}))
                row['sensorPermission']=dict(latest.get('sensor_permission',{}))
            row['ownedProcessResources']=resources
            report['phases'].append(row)
            (a.output/'report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({k:v for k,v in row.items() if k not in {'samples','initial','final','ownedProcessResources'}}),flush=True)
            return row
        trials=[phase(fps,a.phase_seconds,'benchmark') for fps in ((5,10,15,20,30) if a.high_rates else (5,10,15))]
        stable=[x for x in trials if x['deliveredFps']>=x['targetFps']*.85 and x['maxMemoryBytes']<200*1024*1024]
        chosen=10 if any(x['targetFps']==10 for x in stable) else 5
        report['selectedFps']=chosen
        if a.soak_seconds:phase(chosen,a.soak_seconds,'soak')
        with lock:
            (a.output/'first-frame.jpg').write_bytes(frames['first'])
            (a.output/'last-frame.jpg').write_bytes(frames['last'])
            report['previewFrames']=frames['count'];report['distinctPreviewFramesAtLeast']=len(frames['distinct'])
        soak=[x for x in report['phases'] if x['name']=='soak']
        report['ok']=not errors and bool(stable) and all(x['deliveredFps']>=chosen*.85 and x['maxMemoryBytes']<200*1024*1024 for x in soak)
    except Exception as ex:errors.append(str(ex));report['ok']=False
    finally:
        if proc.poll() is None:
            try:send({'op':'quit'});proc.wait(timeout=8)
            except Exception:proc.kill();proc.wait(timeout=3)
        report['workerExitCode']=proc.returncode
        (a.output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'ok':report['ok'],'report':str(a.output/'report.json'),'errors':errors}),flush=True)
    if not report['ok']:raise SystemExit(1)


if __name__=='__main__':main()
