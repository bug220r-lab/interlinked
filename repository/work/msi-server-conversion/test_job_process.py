"""Owned local fixtures for process-tree containment; no tasks or networking."""
import ctypes,hashlib,importlib.util,json,os,subprocess,sys,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
MODULE=HERE.parents[1]/'outputs/Project-Library-2026-10-29/Server-Setup/msi_job_process.py'
spec=importlib.util.spec_from_file_location('job_process_fixture',MODULE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ROOT=Path(tempfile.mkdtemp(prefix='job-process-',dir=HERE));RESULTS=[]
def case(name):p=ROOT/name;p.mkdir();return p
def run(name,fn):
 try:RESULTS.append(dict(test=name,status='PASS',detail=fn()))
 except BaseException as exc:RESULTS.append(dict(test=name,status='FAIL',error=repr(exc)))
def call(root,script,*args,**kw):
 p=root/'entry.py';p.write_text(script,encoding='utf-8')
 with open(root/'stdout','xb') as out,open(root/'stderr','xb') as err:return m.run_process_tree([sys.executable,'-B',str(p),*args],cwd=root,env=os.environ.copy(),stdout=out,stderr=err,**kw)
def alive(pid):
 k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];k.OpenProcess.restype=ctypes.c_void_p;k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];k.WaitForSingleObject.restype=ctypes.c_uint32;k.CloseHandle.argtypes=[ctypes.c_void_p]
 h=k.OpenProcess(0x100000,False,pid)
 if not h:return False
 try:return k.WaitForSingleObject(h,0)==258
 finally:k.CloseHandle(h)
def argv_case():
 root=case('argv');args=['space here','quote"literal','trail\\','a;not-a-command','$(literal)','é']
 code=call(root,'import json,sys;print(json.dumps(sys.argv[1:]));sys.stderr.write("error stream")',*args)
 assert code==0 and json.loads((root/'stdout').read_text())==args and (root/'stderr').read_text()=='error stream';return 'Literal argv and separate output streams preserved without a shell.'
def descendant_case():
 root=case('descendant');script='import subprocess,sys\nfrom pathlib import Path\nsubprocess.Popen([sys.executable,"-c","import time;from pathlib import Path;time.sleep(1.5);Path(\'grandchild-done\').write_text(\'done\')"])\nPath("direct-done").write_text("done")\n'
 started=time.monotonic();code=call(root,script);assert code==0 and (root/'grandchild-done').exists() and time.monotonic()-started>=1.4;return 'Direct child exits first; function waits until descendant finishes its delayed write.'
def cancellation_case():
 root=case('cancel');pids=[]
 def started(pid):pids.append(pid)
 def cancel(active):
  if (root/'grandchild-pid').exists():raise KeyboardInterrupt
 script='import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\nPath("grandchild-pid").write_text(str(p.pid))\ntime.sleep(30)\n'
 try:call(root,script,on_started=started,on_poll=cancel)
 except KeyboardInterrupt:pass
 else:raise AssertionError('Expected cancellation')
 pids.append(int((root/'grandchild-pid').read_text()));assert not any(alive(p) for p in pids);return 'Cancellation terminates direct and descendant processes before returning.'
def start_failure_case():
 root=case('start-failure');pids=[]
 def fail(pid):pids.append(pid);raise RuntimeError('fixture callback failure')
 try:call(root,'from pathlib import Path;Path("must-not-exist").write_text("bad")',on_started=fail)
 except RuntimeError as exc:assert str(exc)=='fixture callback failure'
 else:raise AssertionError('Expected failure')
 assert not (root/'must-not-exist').exists() and not any(alive(p) for p in pids);return 'Failure before resume leaves no running child and executes no user code.'
def breakaway_case():
 root=case('breakaway');script='import subprocess,sys\nfrom pathlib import Path\ntry:\n p=subprocess.Popen([sys.executable,"-c","from pathlib import Path;Path(\'escaped\').write_text(\'bad\')"],creationflags=0x01000000)\n p.wait()\nexcept OSError:\n Path("blocked").write_text("yes")\n'
 assert call(root,script)==0 and (root/'blocked').exists() and not (root/'escaped').exists();return 'CREATE_BREAKAWAY_FROM_JOB is denied.'
def hard_termination_case():
 root=case('hard-stop');entry=root/'entry.py';entry.write_text('import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\nPath("pids.json").write_text(__import__("json").dumps([__import__("os").getpid(),p.pid]))\ntime.sleep(30)\n')
 worker=root/'worker.py';worker.write_text('import importlib.util,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location("primitive",'+repr(str(MODULE))+');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nr=Path('+repr(str(root))+')\nwith open(r/"stdout","xb") as o,open(r/"stderr","xb") as e:m.run_process_tree([sys.executable,"-B",str(r/"entry.py")],cwd=r,env=os.environ.copy(),stdout=o,stderr=e)\n')
 process=subprocess.Popen([sys.executable,'-B',str(worker)],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  deadline=time.monotonic()+10
  while not (root/'pids.json').exists() and time.monotonic()<deadline:time.sleep(.05)
  pids=json.loads((root/'pids.json').read_text());assert all(alive(p) for p in pids)
  process.terminate();process.wait(timeout=10)
  deadline=time.monotonic()+5
  while any(alive(p) for p in pids) and time.monotonic()<deadline:time.sleep(.05)
  assert not any(alive(p) for p in pids)
 finally:
  if process.poll() is None:process.terminate();process.wait(timeout=10)
 return 'Abrupt termination of the synthetic runner closes its sole job handle and terminates both descendants.'
for name,fn in [('argv and output streams',argv_case),('descendant lifetime',descendant_case),('cancellation kills entire tree',cancellation_case),('pre-resume failure',start_failure_case),('breakaway denied',breakaway_case),('hard runner termination',hard_termination_case)]:run(name,fn)
result=dict(moduleSha256=hashlib.sha256(MODULE.read_bytes()).hexdigest(),fixtureRoot=str(ROOT),tests=RESULTS,passed=sum(x['status']=='PASS' for x in RESULTS),failed=sum(x['status']=='FAIL' for x in RESULTS),scope='Only newly created synthetic local child processes; no scheduled tasks, user project data or network.')
(HERE/'job-process-test-results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));sys.exit(bool(result['failed']))
