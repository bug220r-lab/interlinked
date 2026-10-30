"""Owned temporary ledger fixtures only; no shares, media, apps or credentials."""
from pathlib import Path
import argparse,csv,hashlib,importlib.util,io,json,os,sys,threading,uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('missing_under_test',HERE/'interlinked_gui.py')
g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
root=args.output/('missing-fixture-'+uuid.uuid4().hex);root.mkdir(parents=True)
checks=[]
def check(value,label):
    assert value,label
    checks.append(label)
def reject(fn,label):
    try:fn()
    except (ValueError,OSError):checks.append(label)
    else:raise AssertionError(label)
def fresh():
    state=root/uuid.uuid4().hex;state.mkdir();owner=dict(format='interlinked-gui-state-v1',kit=str(root/'kit'),demo=True)
    g.write_json(state/'OWNER.json',owner)
    return g.MissingMediaLedger(state,owner,['project-a','project-b'])
def report(ledger,path='clip.mp4',**kw):
    return ledger.report('project-a',path,request_id=kw.pop('request_id',uuid.uuid4().hex),**kw)

l=fresh();r=report(l,r'C:\never-read\Clip.mp4',source='user_dialog',note='Screenshot spelling uncertain\nUnicode: Ω 🎞')
check(l.snapshot('project-a')['records'][0]['note'].endswith('Ω 🎞'),'Multiline Unicode survives fsync/readback')
check(l.path.read_bytes().endswith(b'\n') and l.marker.exists(),'Header ownership and newline-terminated event durably exist')
restarted=g.MissingMediaLedger(l.state,l.owner,l.projects)
check(restarted.snapshot('project-a')['records'][0]['recordId']==r['record']['recordId'],'Fresh ledger instance reads recorded history')
report(l,r'c:/NEVER-read/clip.MP4')
check(len(l.snapshot('project-a')['records'])==1,'Case and separator normalization dedupes exact project path')
report(l,'Clip.mp4')
check(len(l.snapshot('project-a')['records'])==2,'Filename-only report stays distinct from full path')
l.report('project-b',r'C:\never-read\Clip.mp4',request_id=uuid.uuid4().hex)
check(len(l.snapshot('project-b')['records'])==1 and len(l.snapshot('project-a')['records'])==2,'Same text in different projects stays separate')
request=uuid.uuid4().hex;first=report(l,'idempotent.mp4',request_id=request);before=l.path.read_bytes()
check(report(l,'idempotent.mp4',request_id=request)['duplicateRequest'] and l.path.read_bytes()==before,'Request retry appends no duplicate bytes')
reject(lambda:report(l,'different.mp4',request_id=request),'Request reuse with different payload fails')
key=first['record']['recordId'];l.change_status('project-a',key,'resolved',request_id=uuid.uuid4().hex,note='Found by user')
check(next(x for x in l.snapshot('project-a')['records'] if x['recordId']==key)['status']=='resolved','User resolved status is durable')
reject(lambda:l.change_status('project-b',key,'resolved',request_id=uuid.uuid4().hex),'Status cannot cross project boundary')
report(l,'idempotent.mp4')
check(next(x for x in l.snapshot('project-a')['records'] if x['recordId']==key)['status']=='reported_missing','Report explicitly reopens a resolved exact path')
for text in ['=HYPERLINK("x")',' +SUM(1,2)','@import','－bad','\t=bad']:
    if text.startswith('\t'):text='ordinary.mp4'
    report(l,text,note=' \t=1+1\nSecond line, "quoted"')
csv_value=l.export('project-a','csv');rows=list(csv.reader(io.StringIO(csv_value['text'])))
check(all(cell.startswith('Text: ') for row in rows[1:] for cell in row),'Every spreadsheet display cell has a visible neutral prefix')
raw=json.loads(l.export('project-a','json')['text'])
check(any(x['path']=='=HYPERLINK("x")' for x in raw['records']) and len(raw['events'])>len(raw['records']),'JSON preserves literal text plus complete project event history')
reject(lambda:report(l,'bad\npath'),'Control characters in reported path rejected')
reject(lambda:report(l,'a'*2049),'Oversized path rejected')
reject(lambda:report(l,'clip',note='x'*2001),'Oversized note rejected')
reject(lambda:l.snapshot('unknown-project'),'Unknown project rejected')
reject(lambda:report(l,'clip',source=[]),'Malformed source type rejected')
reject(lambda:l.change_status('project-a',key,[],request_id=uuid.uuid4().hex),'Malformed status type rejected')
reject(lambda:report(l,'clip',request_id='not-a-request'),'Invalid request UUID rejected')
ordinary_calls=[];old_file=l.file
def spy(path,create=False):ordinary_calls.append(Path(path));return old_file(path,create)
l.file=spy
report(l,r'\\server\private\passwords.txt')
check(all(p.parent==l.state for p in ordinary_calls),'Reported UNC text never becomes a file access')

for damage in ('torn','empty','missing','valid-json-change','missing-marker','hardlink'):
    bad=fresh();report(bad);original=bad.path.read_bytes()
    if damage=='torn':bad.path.write_bytes(original+b'{"partial":')
    elif damage=='empty':bad.path.write_bytes(b'')
    elif damage=='missing':bad.path.unlink()
    elif damage=='valid-json-change':
        lines=original.splitlines();event=json.loads(lines[1]);event['note']='corrupt but valid JSON';bad.path.write_bytes(lines[0]+b'\n'+json.dumps(event).encode()+b'\n')
    elif damage=='missing-marker':bad.marker.unlink()
    elif damage=='hardlink':os.link(bad.path,bad.state/'other-link')
    saved=bad.path.read_bytes() if bad.path.exists() else None
    reject(lambda:bad.snapshot('project-a'),damage+' is not returned as an empty successful ledger')
    reject(lambda:report(bad),damage+' blocks future append')
    check((bad.path.read_bytes() if bad.path.exists() else None)==saved,damage+' bytes remain preserved')

initial=fresh();initial.snapshot('project-a');header=initial.path.read_bytes();initial.marker.unlink()
check(initial.snapshot('project-a')['records']==[] and initial.path.read_bytes()==header,'Interrupted header-only initialization reconciles exact identity')
initial.path.unlink();reject(lambda:initial.snapshot('project-a'),'Initialized journal disappearance blocks even without reports')

# Real Controller plus fake busy operation: no child and no project lock needed.
runtime=g.demo_runtime(root/'controller');runtime.audit={'projects':[{'id':'demo-project-1','references':[{'id':'ref-1','path':r'C:\fixture\exact.mp4'}]}]}
c=g.Controller(runtime)
try:
    c.job={'id':'fake-active','status':'WAITING_FOR_MAGIX','action':'open','projectId':'demo-project-1'}
    baseline=c.state()
    c.report_missing(dict(project='demo-project-1',path='user popup.mp4',source='user_dialog',requestId=uuid.uuid4().hex))
    check(c.busy() and c.state()['operation']==baseline['operation'],'Active session remains protected while report is appended')
    c.report_missing(dict(project='demo-project-1',source='audit_flag',auditReferenceId='ref-1',requestId=uuid.uuid4().hex))
    check(c.missing.snapshot('demo-project-1')['records'][1]['path']==r'C:\fixture\exact.mp4','Audit flag derives exact text from selected project reference')
    reject(lambda:c.report_missing(dict(project='demo-project-2',source='audit_flag',auditReferenceId='ref-1',requestId=uuid.uuid4().hex)),'Audit reference cannot cross project boundary')
    reject(lambda:c.report_missing(dict(project='demo-project-1',source='audit_flag',auditReferenceId='ref-1',path='spoofed',requestId=uuid.uuid4().hex)),'Audit flag cannot override trusted reference text')
    reject(lambda:c.report_missing(dict(project='demo-project-1',path='manual',auditReferenceId='ref-1',requestId=uuid.uuid4().hex)),'Manual text cannot assert reference identity')
    check(not runtime.cache.exists(),'Ledger actions never create cache files')
    c.job=None
    entered=threading.Event();resume=threading.Event();quit_done=threading.Event();old_snapshot=c.missing.snapshot
    def paused_snapshot(project):entered.set();assert resume.wait(3);return old_snapshot(project)
    c.missing.snapshot=paused_snapshot
    reader=threading.Thread(target=lambda:c.missing_view('demo-project-1'));reader.start();assert entered.wait(3)
    quitter=threading.Thread(target=lambda:(c.request_quit(),quit_done.set()));quitter.start()
    check(not quit_done.wait(.1),'Quit waits for in-flight report initialization/read transaction')
    resume.set();reader.join(3);quitter.join(3);check(quit_done.is_set(),'Quit completes after report transaction exits')
    reject(lambda:c.report_missing(dict(project='demo-project-1',path='late',requestId=uuid.uuid4().hex)),'Closing gate rejects late report writes')
    reject(lambda:c.missing_view('demo-project-1'),'Closing gate rejects late report initialization/export reads')
finally:c.job=None;c.close()

result=dict(status='PASS',count=len(checks),checks=checks,backendSha256=hashlib.sha256((HERE/'interlinked_gui.py').read_bytes()).hexdigest(),fixtureRoot=str(root),noNetwork=True,noProductionData=True,noApplicationLaunch=True)
(args.output/'missing-media-test-results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
