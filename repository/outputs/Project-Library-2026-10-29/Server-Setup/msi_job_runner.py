"""Owned one-shot MSI jobs in fetched ProjectLibrary cache; no scheduler changes.

The PowerShell entry point registers/starts a uniquely named Interactive-token
task. This worker uses an argv list, redirects child output to owned files, and
holds the library client's cache lock until the entire contained process tree
exits. This is a working-
directory guard, not an OS sandbox for arbitrary code supplied by the user.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

HERE=Path(__file__).absolute().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parent/'Helper'))
import project_library as lib
from msi_job_process import run_process_tree

CACHE=Path(r'C:\Users\jlewi\Documents\Codex\MSI-Working-Projects')
JOBS=HERE.parent/'ServerJobs'/'Jobs'
PYTHON=Path(r'C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
POWERSHELL=Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
CATALOG='msi-project-library-v1'
LIBRARY=r'\\MSI\ProjectLibrary'
RETURNS=r'\\MSI\ProjectReturns'
STAMP=lambda:datetime.now(timezone.utc).isoformat()
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8')

def need(value,message):
    if not value:raise RuntimeError(message)
def job_id(value):
    need(isinstance(value,str) and len(value)==32 and all(c in '0123456789abcdef' for c in value),'Invalid owned job ID')
    return value
def same_unc_share(value,expected):
    # WindowsPath serializes a UNC share root with a trailing backslash. Accept
    # that one separator only; do not resolve aliases, children or dot segments.
    return isinstance(value,str) and value.casefold() in (expected.casefold(),expected.casefold()+'\\')
def cache_project(pid,cache_base=CACHE):
    lib.identifier(pid);root=lib.join(cache_base,pid);lib.directory(root)
    owner=lib.read_json(root/'OWNER.json');state=lib.read_json(root/'state.json')
    need(owner.get('format')=='project-library-owned-v1' and owner.get('projectId')==pid and owner.get('catalogId')==CATALOG,'Cache ownership mismatch')
    need(same_unc_share(owner.get('library'),LIBRARY) and same_unc_share(owner.get('returns'),RETURNS),'Unexpected library/returns identity')
    need(state.get('format')=='project-library-cache-v1' and state.get('projectId')==pid and state.get('catalogId')==CATALOG and state.get('status')=='READY','Cache must be fully fetched and READY before a job runs')
    need(same_unc_share(state.get('library'),LIBRARY) and same_unc_share(state.get('returns'),RETURNS),'Cache state library/returns identity differs from its owner')
    need('pendingSave' not in state,'Resolve the pending cache save before running a job')
    lib.directory(root/'Files')
    return root
def invocation(spec,files,known_python=PYTHON):
    need(spec.get('format')=='msi-job-spec-v1','Unknown job specification')
    kind=spec.get('kind');entry=lib.join(files,spec['entry']);lib.regular(entry)
    relative_work=spec.get('workingDirectory','.')
    work=files if relative_work=='.' else lib.join(files,relative_work);lib.directory(work)
    arguments=spec.get('arguments',[])
    need(isinstance(arguments,list) and all(isinstance(a,str) and '\0' not in a for a in arguments),'Arguments must be a JSON string array without NUL characters')
    need(sum(len(a) for a in arguments)<28000,'Arguments exceed the supported Windows command-line budget')
    if kind=='python':
        need(entry.suffix.lower()=='.py','Python jobs require a .py entry file inside Files');lib.regular(known_python);argv=[str(known_python),'-B',str(entry),*arguments]
    elif kind=='powershell':
        need(entry.suffix.lower()=='.ps1','PowerShell jobs require a .ps1 entry file inside Files');lib.regular(POWERSHELL)
        argv=[str(POWERSHELL),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(entry),*arguments]
    elif kind=='exe':
        need(entry.suffix.lower()=='.exe','Native jobs require an .exe inside Files');argv=[str(entry),*arguments]
    else:raise RuntimeError('Job kind must be python, powershell, or exe')
    environment=os.environ.copy();environment['PYTHONUNBUFFERED']='1';environment['PYTHONDONTWRITEBYTECODE']='1'
    hints=spec.get('environmentPaths',{})
    need(isinstance(hints,dict) and set(hints).issubset({'STATS_DATA_DIR','STATS_IMPORT_DIR'}),'Only managed Stats data/import directory overrides are supported')
    for name,relative_path in hints.items():
        target=lib.join(files,relative_path);lib.directory(target);environment[name]=str(target)
    return argv,work,entry,environment
def prepare(spec_path,cache_base=CACHE,jobs_base=JOBS,known_python=PYTHON):
    spec_path=lib.absolute(spec_path);cache_base=lib.absolute(cache_base)
    need(spec_path.is_relative_to(cache_base),'Job specification must be inside the managed cache before it can be read')
    relative_spec=spec_path.relative_to(cache_base)
    need(len(relative_spec.parts)>=3 and relative_spec.parts[1]=='Files','Job specification must be inside a project Files tree')
    pid=relative_spec.parts[0];root=cache_project(pid,cache_base);lib.regular(spec_path);spec=lib.read_json(spec_path)
    need(spec.get('projectId')==pid,'Job specification project ID does not match its containing managed cache')
    need(spec_path.is_relative_to(root/'Files'),'Job specification must be inside this fetched project Files tree')
    with lib.exclusive(root/'.client.lock',create=True):
        cache_project(spec['projectId'],cache_base)
        argv,work,entry,_=invocation(spec,root/'Files',known_python)
        jid=uuid.uuid4().hex;folder=lib.join(jobs_base,jid);lib.mkdir(jobs_base);need(not lib.exists(folder),'Fresh job path already exists');os.mkdir(lib.fs(folder))
        record=dict(format='msi-job-record-v1',jobId=jid,projectId=spec['projectId'],catalogId=CATALOG,createdUtc=STAMP(),spec=spec,entrySha256=lib.file_digest(entry),cacheStateSha256=lib.file_digest(root/'state.json'),originalSpec=str(spec_path),cacheRoot=str(root),workingDirectory=str(work),taskName='MSICodingJob-'+jid,taskPath='\\',runner=str(Path(__file__).absolute()),knownPython=str(known_python),description='One-shot user-requested job; no periodic trigger or stored credentials.')
        lib.atomic_json(folder/'job.json',record,replace=False)
        lib.atomic_json(folder/'status.json',dict(format='msi-job-status-v1',jobId=jid,projectId=spec['projectId'],status='PREPARED_NOT_REGISTERED',updatedUtc=STAMP()),replace=False)
        return dict(status='PREPARED_NOT_REGISTERED',jobId=jid,jobDirectory=str(folder),taskName=record['taskName'],projectId=spec['projectId'],workingDirectory=str(work))
def load_job(jid,jobs_base=JOBS):
    folder=lib.join(jobs_base,job_id(jid));lib.directory(folder);record=lib.read_json(folder/'job.json')
    need(record.get('format')=='msi-job-record-v1' and record.get('jobId')==jid and record.get('catalogId')==CATALOG,'Job ownership mismatch')
    need(record.get('taskName')=='MSICodingJob-'+jid,'Task name ownership mismatch')
    return folder,record
def _run_locked(jid,cache_base=CACHE,jobs_base=JOBS,known_python=PYTHON):
    folder,record=load_job(jid,jobs_base);status_path=folder/'status.json';previous=lib.read_json(status_path)
    need(previous.get('status')=='PREPARED_NOT_REGISTERED','A job runs once; prepare a new job instead of rerunning an old receipt')
    status=dict(format='msi-job-status-v1',jobId=jid,projectId=record['projectId'],status='STARTING',runnerPid=os.getpid(),startedUtc=STAMP(),updatedUtc=STAMP(),childPid=None,exitCode=None,error=None,cacheLockHeld=False)
    lib.atomic_json(status_path,status)
    try:
        root=cache_project(record['projectId'],cache_base)
        need(str(root).casefold()==record['cacheRoot'].casefold(),'Job cache path changed')
        with lib.exclusive(root/'.client.lock',create=True):
            cache_project(record['projectId'],cache_base)
            need(lib.file_digest(root/'state.json')==record['cacheStateSha256'],'Cache snapshot/state changed since job preparation; prepare a fresh job')
            argv,work,entry,environment=invocation(record['spec'],root/'Files',known_python)
            need(lib.file_digest(entry)==record['entrySha256'],'Job entry file changed since preparation; create a new job request')
            status.update(status='RUNNING',cacheLockHeld=True,updatedUtc=STAMP(),stdout=str(folder/'stdout.log'),stderr=str(folder/'stderr.log'))
            # CreateNew logs and argv lists avoid overwriting logs or invoking a shell.
            with open(lib.fs(folder/'stdout.log'),'xb') as stdout,open(lib.fs(folder/'stderr.log'),'xb') as stderr:
                def started(pid):
                    status['childPid']=pid;status['updatedUtc']=STAMP();lib.atomic_json(status_path,status)
                status['exitCode']=run_process_tree(argv,cwd=work,env=environment,stdout=stdout,stderr=stderr,on_started=started)
            status.update(status='COMPLETED' if status['exitCode']==0 else 'CHILD_FAILED',cacheLockHeld=False,finishedUtc=STAMP(),updatedUtc=STAMP())
            lib.atomic_json(status_path,status)
    except BaseException as exc:
        status.update(status='RUNNER_FAILED_REVIEW_REQUIRED',cacheLockHeld=False,error=str(exc),finishedUtc=STAMP(),updatedUtc=STAMP())
        lib.atomic_json(status_path,status)
        raise
    return status
def run(jid,cache_base=CACHE,jobs_base=JOBS,known_python=PYTHON):
    folder,_=load_job(jid,jobs_base)
    with lib.exclusive(folder/'.runner.lock',create=True):
        return _run_locked(jid,cache_base,jobs_base,known_python)
def status(jid,jobs_base=JOBS):
    folder,record=load_job(jid,jobs_base)
    return dict(jobId=jid,taskName=record['taskName'],taskPath=record['taskPath'],jobDirectory=str(folder),record=lib.read_json(folder/'status.json'))
def main():
    p=argparse.ArgumentParser(description=__doc__);group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare');group.add_argument('--run');group.add_argument('--status')
    args=p.parse_args()
    try:
        result=prepare(args.prepare) if args.prepare else run(args.run) if args.run else status(args.status)
        print(json.dumps(result,ensure_ascii=False));return 0 if result.get('exitCode') in (None,0) else 1
    except Exception as exc:print(json.dumps(dict(status='JOB_HELPER_FAILED',error=str(exc))),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
