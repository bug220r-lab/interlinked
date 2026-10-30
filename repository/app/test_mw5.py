"""Synthetic MW5 transfer fixtures only; never inspects/launches a real editor."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); sys.modules[name] = value; spec.loader.exec_module(value)
    return value
e = module('mw5_test_engine', HERE.parent/'outputs/Project-Library-2026-10-29/Client/project_library.py')
m = module('mw5_test_companion', HERE/'interlinked_mw5.py')
parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
root = args.output.resolve()/('mw5-'+uuid.uuid4().hex[:8]); e.mkdir(root)
checks = []
def check(ok, text):
    assert ok, text
    checks.append(text)
def rejects(fn, text):
    try: fn()
    except (ValueError, OSError, e.LibraryError): checks.append(text)
    else: raise AssertionError(text)
def write(path, data):
    e.mkdir(path.parent)
    with open(e.fs(path), 'wb') as stream: stream.write(data)
def read(path):
    with open(e.fs(path), 'rb') as stream: return stream.read()
def fixture():
    base = root/uuid.uuid4().hex[:8]
    files = base/'cache/Files'; editor = base/'editor'; plugins = editor/'MW5Mercs/Plugins'; state = base/'state'
    e.mkdir(files); e.mkdir(plugins)
    sources = {name: files/'Plugins'/name for name in m.PLUGINS}
    for name, folder in sources.items():
        write(folder/'Content/one.uasset', ('fixture '+name).encode())
        write(folder/'old.txt', b'old fixture')
    exe = editor/'UE4Editor.exe'; project = editor/'MW5Mercs/MW5Mercs.uproject'
    write(exe, b'not an executable -- synthetic only'); write(project, b'{}')
    info = dict(available=True, install=str(editor), executable=str(exe), project=str(project),
                pluginRoot=str(plugins), executableSha256=e.file_digest(exe))
    workspace = m.MW5Workspace(e, state, files, sources, info, process_guard=lambda: None)
    return workspace, base, files, plugins, sources, info

w, base, files, plugins, sources, info = fixture()
rejects(lambda:m.find_plugin_sources(files, e), 'Bare same-name asset directories without native descriptors are refused')
discovery_files=base/'discovery/Files'
native=discovery_files/'Drive_C/Program Files/Epic Games/MechWarrior5Editor/MW5Mercs/Plugins'
discovery_sources={name:native/name for name in m.PLUGINS}
for name, folder in discovery_sources.items():
    write(folder/(name+'.uplugin'), b'{"FileVersion":3}')
    write(folder/'Content/authored.uasset', b'authored native fixture')
write(discovery_sources[m.PLUGINS[0]]/'Cooked/keep.bin', b'preserve actual plugin content')
write(discovery_sources[m.PLUGINS[0]]/'Saved/recovery.bin', b'preserve actual recovery content')
cooked=discovery_files/'workspace/research/evidence/Cooked/WindowsNoEditor/MW5Mercs/Plugins'/m.PLUGINS[0]
write(cooked/'Content/historical.uasset', b'preserve cooked evidence')
check(m.find_plugin_sources(discovery_files,e)=={k:str(v) for k,v in discovery_sources.items()},
      'Exact native descriptors select authored plugins while descriptor-free cooked evidence remains separate')
discovery_workspace=m.MW5Workspace(e,base/'discovery-state',discovery_files,discovery_sources,info,process_guard=lambda:None)
inventory=discovery_workspace._inventory(discovery_sources)
check(m.PLUGINS[0]+'/Cooked/keep.bin' in inventory and m.PLUGINS[0]+'/Saved/recovery.bin' in inventory,
      'Accepted plugins retain generated-looking and recovery members in the full inventory')
check(read(cooked/'Content/historical.uasset')==b'preserve cooked evidence', 'Discovery preserves historical cooked bytes')
descriptor=discovery_sources[m.PLUGINS[1]]/(m.PLUGINS[1]+'.uplugin')
os.rename(e.fs(descriptor),e.fs(descriptor.with_suffix('.saved')))
rejects(lambda:m.find_plugin_sources(discovery_files,e), 'Missing exact native descriptor refuses discovery without archive fallback')
os.rename(e.fs(descriptor.with_suffix('.saved')),e.fs(descriptor))
descriptor_alias=descriptor.with_suffix('.alias')
os.link(e.fs(descriptor),e.fs(descriptor_alias))
rejects(lambda:m.find_plugin_sources(discovery_files,e), 'Hardlinked native descriptor is refused')
os.unlink(e.fs(descriptor_alias))
write(cooked/(m.PLUGINS[0]+'.uplugin'),b'{"FileVersion":3}')
rejects(lambda:m.find_plugin_sources(discovery_files,e), 'Multiple descriptor-qualified plugin roots remain ambiguous and refused')
os.unlink(e.fs(cooked/(m.PLUGINS[0]+'.uplugin')))
manifests=base/'manifests'; e.mkdir(manifests)
e.atomic_json(manifests/'other.item', dict(DisplayName='Unrelated game', InstallLocation='invalid'))
check(not m.discover_editor(manifests, e)['available'], 'Unrelated Epic manifests never become editor paths')
e.atomic_json(manifests/'editor.item', dict(DisplayName='MechWarrior 5 Editor', InstallLocation=info['install'], LaunchExecutable='UE4Editor.exe'))
check(m.discover_editor(manifests, e)['executableSha256'] == info['executableSha256'], 'Matching Epic editor and executable validated')
e.atomic_json(manifests/'second.item', dict(DisplayName='MechWarrior 5 Editor', InstallLocation=info['install'], LaunchExecutable='UE4Editor.exe'))
rejects(lambda: m.discover_editor(manifests, e), 'Ambiguous editor installs require review')
fixed_exe=Path(info['install'])/'Engine/Binaries/Win64/UE4Editor.exe';write(fixed_exe,b'reviewed fixture executable')
reviewed=base/'reviewed-editor.json'
review=dict(format='interlinked-mw5-reviewed-editor-v1',install=info['install'],executableSha256=e.file_digest(fixed_exe),confirmedByUser=True)
e.atomic_json(reviewed,review)
discovered=m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=reviewed)
check(discovered['available'] and discovered['source']=='reviewed-local' and discovered['executable']==str(fixed_exe), 'Explicit user-confirmed review receipt supports metadata-free fixed editor layout')
check(not m.discover_editor(base/'no-epic-metadata',e)['available'], 'Absent review receipt never enables an automatic editor path fallback')
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=base/'absent-review.json'), 'Missing explicit review configuration is refused')
bad_review=base/'bad-review.json';e.atomic_json(bad_review,dict(review,confirmedByUser=False))
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=bad_review), 'Unconfirmed local editor receipt is refused')
e.atomic_json(bad_review,dict(review,executable='unreviewed.exe'))
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=bad_review), 'Extra executable path field cannot widen reviewed editor configuration')
write(fixed_exe,b'updated after review')
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=reviewed), 'Changed reviewed editor executable hash blocks fallback')
write(fixed_exe,b'reviewed fixture executable')
os.link(e.fs(reviewed),e.fs(base/'hardlinked-review.json'))
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=reviewed), 'Hardlinked private editor receipt is refused')
fresh_review=base/'fresh-review.json';e.atomic_json(fresh_review,review)
junction=base/'editor-junction'
junction_env=dict(os.environ,INTERLINKED_TEST_JUNCTION=str(junction),INTERLINKED_TEST_TARGET=info['install'])
subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-Command',
                "New-Item -ItemType Junction -Path $env:INTERLINKED_TEST_JUNCTION -Target $env:INTERLINKED_TEST_TARGET | Out-Null"],
               env=junction_env,check=True,capture_output=True)
e.atomic_json(fresh_review,dict(review,install=str(junction)))
rejects(lambda:m.discover_editor(base/'no-epic-metadata',e,reviewed_config_path=fresh_review), 'Reviewed installation junction is refused before executable access')
rejects(lambda: m.MW5Workspace(e, base/'other-state', files, {m.PLUGINS[0]:base/'outside',m.PLUGINS[1]:sources[m.PLUGINS[1]]}, info, process_guard=lambda:None), 'Source tree outside managed Files refused')

first=m.PLUGINS[0]; second=m.PLUGINS[1]
write(plugins/first/'Content/one.uasset', b'previous editor bytes')
write(plugins/first/'editor-only.txt', b'preserve me')
preview=w.preview_stage()
check(preview['applicable'] and len(preview['changes']) == 4, 'Staging preview enumerates reviewed copies')
check(read(plugins/first/'Content/one.uasset') == b'previous editor bytes', 'Preview never mutates editor files')
with e.exclusive(files.parent/'.client.lock'): pass
checks.append('Idle preview releases the cache lock')
result=w.apply_stage(preview['previewId'])
check(result['status']=='STAGED' and not result['automaticSave'], 'Explicit staging creates protected session without save-back')
check(read(plugins/first/'Content/one.uasset') == read(sources[first]/'Content/one.uasset'), 'Staging copies authored asset bytes exactly')
check(read(plugins/first/'editor-only.txt') == b'preserve me', 'Editor-only files remain intact')
backup=w.state_root/'transactions'/preview['previewId']/'Backup'/first/'Content/one.uasset'
check(read(backup) == b'previous editor bytes', 'Differing editor bytes have independent verified backup')
rejects(lambda: e.exclusive(files.parent/'.client.lock').__enter__(), 'Staged session holds authoritative cache lock')
rejects(lambda: m.MW5Workspace(e,w.state_root,files,sources,info,process_guard=lambda:None), 'Second adapter cannot bypass held cache lock')
rejects(w.finish_session, 'Finishing requires explicit collection first')
class Process:
    code=None
    def poll(self): return self.code
process=Process(); launched=[]
def launch(argv, **kw): launched.append((argv,kw)); return process
check(w.start_session(launch)['status']=='EDITING', 'Editor session starts after staging')
check(launched == [([info['executable'],info['project']],dict(cwd=info['install'],shell=False))], 'Launch uses fixed executable/project arguments without shell')
rejects(w.preview_collect, 'Running editor child blocks collection')
process.code=0
write(plugins/first/'Content/one.uasset', b'editor changed asset')
write(sources[first]/'Content/one.uasset', b'managed changed asset')
conflict=w.preview_collect()
check(not conflict['applicable'] and len(conflict['conflicts'])==1, 'Concurrent managed/editor changes produce explicit conflict')
rejects(lambda:w.apply_collect(conflict['previewId']), 'Conflict cannot be applied')
check(read(sources[first]/'Content/one.uasset')==b'managed changed asset' and read(plugins/first/'Content/one.uasset')==b'editor changed asset', 'Both conflicting variants remain intact')
write(sources[first]/'Content/one.uasset', ('fixture '+first).encode())
os.unlink(e.fs(plugins/first/'old.txt'))
write(plugins/second/'Content/new Ω.uasset', b'new editor fixture')
collect=w.preview_collect()
check({row['action'] for row in collect['changes']}=={'add','replace','delete'}, 'Collection preview includes additions modifications and deletions')
write(plugins/second/'Content/new Ω.uasset', b'changed after preview')
rejects(lambda:w.apply_collect(collect['previewId']), 'Stale source preview fails before managed writes')
check(read(sources[first]/'Content/one.uasset')==('fixture '+first).encode(), 'Stale preview preserves current managed bytes')
collect=w.preview_collect(); old_source=read(sources[first]/'Content/one.uasset')
done=w.apply_collect(collect['previewId'])
check(done['status']=='COLLECTED' and not done['automaticSave'], 'Collection finishes locally without remote save')
check(read(sources[first]/'Content/one.uasset')==b'editor changed asset', 'Modified asset collected byte-for-byte')
check(not e.exists(sources[first]/'old.txt') and read(sources[second]/'Content/new Ω.uasset')==b'changed after preview', 'Explicit deletion and Unicode addition applied')
check(read(w.state_root/'transactions'/collect['previewId']/'Backup'/first/'Content/one.uasset')==old_source, 'Previous managed asset preserved before replacement')
check(read(w.state_root/'transactions'/collect['previewId']/'Backup'/first/'old.txt')==b'old fixture', 'Deleted managed file preserved independently')
rejects(lambda:e.exclusive(files.parent/'.client.lock').__enter__(), 'Collected session retains cache lock until Done')
check(w.finish_session()['status']=='DONE_LOCAL_COPY_RETAINED', 'Explicit session finish retains local working tree')
with e.exclusive(files.parent/'.client.lock'): pass
checks.append('Done releases the cache lock for explicit save/verify')
check(w.state()['status']=='IDLE' and len(list((w.state_root/'sessions').glob('*.json')))==1, 'Completed session receipt retained outside project files')

w2, base2, files2, plugins2, sources2, info2=fixture()
stale=w2.preview_stage(); write(sources2[first]/'new.txt',b'late addition')
rejects(lambda:w2.apply_stage(stale['previewId']), 'Managed changes invalidate staging preview')
with e.exclusive(files2.parent/'.client.lock'): pass
checks.append('Rejected preflight cannot leave phantom session lock')
fresh=w2.preview_stage()
def fault(phase,key): raise OSError('synthetic crash after first committed file')
w2.fault_hook=fault
rejects(lambda:w2.apply_stage(fresh['previewId']), 'Interrupted application leaves recoverable transaction')
check(w2.state()['status']=='APPLYING', 'Interrupted transfer is not reported as success')
check(w2.resume_preview()['previewId']==fresh['previewId'] and w2.state()['resumableDirection']=='stage', 'Recovery exposes the exact interrupted preview without accepting caller paths')
w2.fault_hook=None
check(w2.apply_stage(fresh['previewId'])['status']=='STAGED', 'Exact interrupted staging preview resumes safely')
check(w2.resume_preview() is None, 'Completed transfers are not exposed as resumable previews')
empty=w2.preview_collect(); w2.apply_collect(empty['previewId']); w2.finish_session()

w3, base3, files3, plugins3, sources3, info3=fixture()
low=w3.preview_stage(); original_space=e.space
e.space=lambda *a,**kw: (_ for _ in ()).throw(e.LibraryError('Insufficient free space fixture'))
try: rejects(lambda:w3.apply_stage(low['previewId']), 'Low free space blocks transfer')
finally:e.space=original_space
check(not e.exists(plugins3/first/'Content/one.uasset'), 'Low space writes no editor payload')
check(w3.apply_stage(low['previewId'])['status']=='STAGED', 'Space-blocked owned transaction resumes without new approval bytes')
cp=w3.preview_collect();w3.apply_collect(cp['previewId']);w3.finish_session()

w4,base4,files4,plugins4,sources4,info4=fixture()
write(info4 and Path(info4['executable']), b'updated executable')
rejects(w4.preview_stage, 'Editor update requires new reviewed executable identity')
w5,base5,files5,plugins5,sources5,info5=fixture()
w5.process_guard=lambda:(_ for _ in ()).throw(ValueError('editor open fixture'))
rejects(w5.preview_stage, 'Detected editor process refuses staging')
w5.process_guard=lambda:None
lock=e.open_handle(sources5[first]/'Content/one.uasset',0x40000000,0,3,0)
try:rejects(w5.preview_stage,'Locked authored file blocks preview instead of skipping it')
finally:e.native().CloseHandle(lock)
os.link(e.fs(sources5[first]/'old.txt'),e.fs(sources5[first]/'linked.txt'))
rejects(w5.preview_stage, 'Hardlinked plugin files refused')
rejects(lambda:w5.apply_stage('../escape'), 'Traversal cannot select a preview')
check(not e.exists(root/'escape'), 'No transfer writes outside reviewed plugin roots')

w6,base6,files6,plugins6,sources6,info6=fixture()
write(plugins6/first/'Content',b'file occupying a directory')
collision=w6.preview_stage()
check(not collision['applicable'] and bool(collision['conflicts']), 'File/directory collision blocks preview before transfer')
rejects(lambda:w6.apply_stage(collision['previewId']), 'Collision preview cannot mutate editor or capture a session')
with e.exclusive(files6.parent/'.client.lock'):pass
checks.append('Rejected collision leaves cache lock available')

w7,base7,files7,plugins7,sources7,info7=fixture()
st=w7.preview_stage();w7.apply_stage(st['previewId'])
rejects(lambda:w7.start_session(lambda *a,**k:(_ for _ in ()).throw(OSError('synthetic executable launch failure'))), 'Editor launch failure is reported')
check(w7.state()['status']=='STAGED', 'Failed launch retains staged session and cache protection')
cp=w7.preview_collect();w7.apply_collect(cp['previewId']);w7.finish_session()

w8,base8,files8,plugins8,sources8,info8=fixture()
st=w8.preview_stage();w8.apply_stage(st['previewId'])
w8._release_cache() # Synthetic process-loss boundary; the journal remains STAGED.
class CountedLease:
    def __init__(self,inner):self.inner=inner;self.enters=0;self.exits=0
    def __enter__(self):self.enters+=1;return self.inner.__enter__()
    def __exit__(self,*args):self.exits+=1;return self.inner.__exit__(*args)
lease=CountedLease(e.exclusive(files8.parent/'.client.lock'));lease.__enter__()
bad_info=dict(info8,executableSha256='0'*64)
rejects(lambda:m.MW5Workspace(e,w8.state_root,files8,sources8,bad_info,process_guard=lambda:None,cache_guard=lease), 'Recovery constructor validates ownership before adopting caller cache lease')
check(lease.enters==1 and lease.exits==0, 'Failed recovery constructor neither enters nor releases caller lease')
rejects(lambda:e.exclusive(files8.parent/'.client.lock').__enter__(), 'Caller lease remains authoritative after constructor validation failure')
adopted=m.MW5Workspace(e,w8.state_root,files8,sources8,info8,process_guard=lambda:None,cache_guard=lease)
check(adopted.state()['status']=='STAGED' and lease.enters==1 and lease.exits==0, 'Outstanding session adopts held cache lease without a release/acquire gap')
cp=adopted.preview_collect();adopted.apply_collect(cp['previewId']);adopted.finish_session()
check(lease.exits==1, 'Explicit recovered-session finish releases adopted cache lease exactly once')
with e.exclusive(files8.parent/'.client.lock'):pass
checks.append('Recovered cache becomes available only after explicit finish')
idle_lease=CountedLease(e.exclusive(files8.parent/'.client.lock'));idle_lease.__enter__()
rejects(lambda:m.MW5Workspace(e,w8.state_root,files8,sources8,info8,process_guard=lambda:None,cache_guard=idle_lease), 'Idle journal refuses adoption of an unrelated recovery lease')
check(idle_lease.exits==0, 'Rejected idle-session adoption leaves caller lease untouched')
idle_lease.__exit__(None,None,None)

receipt=dict(status='PASS',count=len(checks),checks=checks,scope='Synthetic MW5 plugin fixtures; no actual editor or production data',
             companionSha256=e.file_digest(HERE/'interlinked_mw5.py'))
result=root/'mw5-test-results.json'; e.atomic_json(result,receipt)
print(json.dumps(dict(status='PASS',count=len(checks),receipt=str(result))))
