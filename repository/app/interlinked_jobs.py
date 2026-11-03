"""Read-only MSI job observations, collected only after an explicit refresh.

The native SSH prompt owns authentication. No password, SSH stderr, remote
command, job specification, or project content enters the cached observation.
The worker reads existing MSI records and bounded log tails; it never starts,
cancels, retries, saves, releases, uploads or changes a remote job.
"""
from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
import zlib

KIT = Path(r'C:\Users\Jude\Documents\MSI-Project-Library')
SYSTEM_SSH = Path(r'C:\Windows\System32\OpenSSH\ssh.exe')
MSI_SID = 'S-1-5-21-3086848077-1833151167-136462239-1001'
PUBLIC_KEY = 'AAAAC3NzaC1lZDI1NTE5AAAAIM2hEErQIByIkaxhMwupU08Pjy2bcyNwl4pvWMyEpyAz'
FINGERPRINT = 'SHA256:oJliv6DyXoZZERIXw7FSD7ekI/PqxSLU5YPv5d5ApiA'
# Public pin provenance: reviewed SSH plan SHA256
# 32de6b83e91a000541eb9910656cd33486c1096859e2bce593474ae48d9ea010.
# No consumed acceptance controller or plan is executed by this module.
MAX_JOBS = 50
LOG_BYTES = 32768
MAX_RESPONSE = 5 * 1024 * 1024
STALE_SECONDS = 60
QUERY_TIMEOUT = 180
FAILURE_DISPLAY_SECONDS = 60
JOB_ID = re.compile(r'[0-9a-f]{32}\Z')
PROJECT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
DEVICE = re.compile(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z', re.I)
STATES = {'PREPARED_NOT_REGISTERED', 'STARTING', 'RUNNING', 'COMPLETED',
          'CHILD_FAILED', 'RUNNER_FAILED_REVIEW_REQUIRED'}
TASK_STATES = {'Unknown', 'NotRegistered', 'Disabled', 'Queued', 'Ready', 'Running'}


def need(value, message):
    if not value:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def timestamp(value):
    need(isinstance(value, str) and len(value) <= 48, 'Invalid job observation time.')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.tzinfo is not None, 'Job observation time needs a timezone.')
    return parsed.timestamp()


def ordinary(path, missing=False):
    path = Path(path)
    need(path.is_absolute(), 'Absolute jobs path required.')
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        attrs = getattr(info, 'st_file_attributes', 0)
        if os.name == 'nt' and attrs & 0x40000:
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
            kernel.GetFileAttributesW.restype = ctypes.c_uint32
            actual = kernel.GetFileAttributesW(str(item))
            if actual == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            attrs = (attrs & ~0x40000) | actual
        need(not stat.S_ISLNK(info.st_mode) and not attrs & 0x441400,
             'Linked or cloud jobs paths are refused.')
        need(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1,
             'Hardlinked job metadata is refused.')
    return path


def system_ssh(path):
    """Validate the fixed Windows client, separately from single-link metadata.

    Windows servicing hardlinks this OS binary into WinSxS. That is valid for
    this exact System32 executable; job metadata still requires one hardlink.
    """
    path = Path(path)
    need(path == SYSTEM_SSH, 'Use the reviewed Windows SSH client.')
    ordinary(path.parent)
    info = path.lstat()
    attrs = getattr(info, 'st_file_attributes', 0)
    need(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
         and not attrs & 0x441400, 'The Windows SSH client must be an ordinary local executable.')
    return path


def read_json(path, limit=MAX_RESPONSE):
    path = ordinary(path)
    leaf = path.lstat()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
         'Ordinary job metadata required.')
    ordinary(path)
    current = path.lstat()
    need((leaf.st_dev, leaf.st_ino) == (before.st_dev, before.st_ino) ==
         (current.st_dev, current.st_ino), 'Job observation path changed while reading.')
    need(len(data) <= limit, 'Job observation exceeds its size limit.')
    need((before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_ino, after.st_size, after.st_mtime_ns),
         'Job observation changed while reading.')
    value = json.loads(data.decode('utf-8-sig'))
    need(isinstance(value, dict), 'Job observation must be an object.')
    return value


def write_json(path, value):
    ordinary(path.parent)
    ordinary(path, missing=True)
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    need(len(data) <= MAX_RESPONSE, 'Job observation exceeds its size limit.')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    ordinary(path, missing=True)
    os.replace(temporary, path)


def validate_snapshot(value, query_id=None):
    """Accept only reduced observations, never executable paths/specifications."""
    need(isinstance(value, dict) and set(value) == {
        'format', 'queryId', 'host', 'sid', 'observedAt', 'jobs', 'truncated', 'rejected'},
        'Unexpected MSI job observation schema.')
    need(value['format'] == 'interlinked-msi-jobs-v1' and value['host'] == 'MSI'
         and value['sid'] == MSI_SID, 'Unexpected MSI job observation identity.')
    need(isinstance(value['queryId'], str) and JOB_ID.fullmatch(value['queryId']),
         'Invalid job query identity.')
    if query_id is not None:
        need(value['queryId'] == query_id, 'Job observation belongs to another query.')
    timestamp(value['observedAt'])
    need(type(value['truncated']) is bool and type(value['rejected']) is int
         and 0 <= value['rejected'] <= 100000, 'Invalid job observation counts.')
    need(isinstance(value['jobs'], list) and len(value['jobs']) <= MAX_JOBS,
         'Too many MSI job observations.')
    seen = set()
    for row in value['jobs']:
        need(isinstance(row, dict) and set(row) == {
            'jobId', 'projectId', 'status', 'exitCode', 'createdUtc', 'startedUtc',
            'updatedUtc', 'finishedUtc', 'taskState', 'lastTaskResult', 'logs'},
            'Unexpected job row schema.')
        jid = row['jobId']
        need(isinstance(jid, str) and JOB_ID.fullmatch(jid) and jid not in seen,
             'Invalid or duplicate job ID.')
        seen.add(jid)
        need(isinstance(row['projectId'], str) and PROJECT_ID.fullmatch(row['projectId'])
             and not DEVICE.fullmatch(row['projectId']),
             'Invalid job project ID.')
        need(isinstance(row['status'], str) and row['status'] in STATES
             and isinstance(row['taskState'], str) and row['taskState'] in TASK_STATES,
             'Unknown job or scheduler state.')
        for key in ('exitCode', 'lastTaskResult'):
            number = row[key]
            need(number is None or type(number) is int and -(2 ** 31) <= number <= 2 ** 32 - 1,
                 'Invalid job exit result.')
        for key in ('createdUtc', 'updatedUtc'):
            timestamp(row[key])
        for key in ('startedUtc', 'finishedUtc'):
            if row[key] is not None:
                timestamp(row[key])
        need(isinstance(row['logs'], dict) and set(row['logs']) == {'stdout', 'stderr'},
             'Unexpected job log streams.')
        for stream in row['logs'].values():
            need(isinstance(stream, dict) and set(stream) == {
                'available', 'bytes', 'truncated', 'dataBase64'}, 'Unexpected log schema.')
            need(type(stream['available']) is bool and type(stream['truncated']) is bool
                 and type(stream['bytes']) is int and 0 <= stream['bytes'] <= 2 ** 63 - 1,
                 'Invalid log metadata.')
            encoded = stream['dataBase64']
            need(isinstance(encoded, str) and len(encoded) <= 4 * ((LOG_BYTES + 2) // 3),
                 'Job log tail exceeds its limit.')
            data = base64.b64decode(encoded, validate=True)
            need(len(data) <= LOG_BYTES and len(data) <= stream['bytes'], 'Invalid log tail size.')
            need(base64.b64encode(data).decode('ascii') == encoded
                 and (not stream['available'] or len(data) == min(stream['bytes'], LOG_BYTES)),
                 'Incomplete or noncanonical log tail.')
            need(stream['available'] or not data and stream['bytes'] == 0 and not stream['truncated'],
                 'Unavailable log includes content.')
            need(stream['truncated'] == (stream['bytes'] > len(data)), 'Invalid log truncation marker.')
    return copy.deepcopy(value)


def warning_for(row):
    if row['taskState'] == 'Unknown':
        return 'Scheduler state could not be read; worker readiness is unknown.'
    if row['status'] in {'STARTING', 'RUNNING'} and row['taskState'] != 'Running':
        return 'Runner receipt and scheduler disagree. Review before saving or releasing its MSI working copy.'
    if row['status'] == 'COMPLETED' and (row['exitCode'] != 0 or row['taskState'] != 'Ready'
                                         or row['lastTaskResult'] != 0 or not row['startedUtc'] or not row['finishedUtc']):
        return 'Completion receipt is not confirmed by a successful ready scheduler task.'
    if row['status'] == 'RUNNER_FAILED_REVIEW_REQUIRED':
        return 'Runner failure needs review; this observation does not retry or clean up the job.'
    return ''


# Executed from memory using the existing MSI Python runtime (-I -B). It does
# not import the job runner or call any start/cancel/save/release implementation.
REMOTE_READER = r'''
import base64, csv, ctypes, datetime, io, json, os, pathlib, re, stat, subprocess
ROOT = pathlib.Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep\outputs\Project-Library-2026-10-29\ServerJobs\Jobs')
SID = 'S-1-5-21-3086848077-1833151167-136462239-1001'
ID = re.compile(r'[0-9a-f]{32}\Z')
PID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
DEVICE = re.compile(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z',re.I)
STATES = {'PREPARED_NOT_REGISTERED','STARTING','RUNNING','COMPLETED','CHILD_FAILED','RUNNER_FAILED_REVIEW_REQUIRED'}
def verify_identity():
    if os.name!='nt':raise ValueError('Windows MSI required')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    name=ctypes.create_unicode_buffer(256);size=ctypes.c_uint32(256)
    kernel.GetComputerNameW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_uint32)]
    if not kernel.GetComputerNameW(name,ctypes.byref(size)) or name.value.casefold()!='msi':raise ValueError('remote machine differs')
    result=subprocess.run([r'C:\Windows\System32\whoami.exe','/user','/fo','csv','/nh'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or len(result.stdout)>4096:raise ValueError('remote identity unavailable')
    rows=list(csv.reader(io.StringIO(result.stdout.decode('utf-8-sig','replace'))))
    if len(rows)!=1 or len(rows[0])!=2 or rows[0][1]!=SID:raise ValueError('remote account differs')
def plain(path):
    for item in [*reversed(path.parents),path]:
        info=item.lstat(); attrs=getattr(info,'st_file_attributes',0)
        if os.name=='nt' and attrs & 0x40000:
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.GetFileAttributesW.argtypes=[ctypes.c_wchar_p];k.GetFileAttributesW.restype=ctypes.c_uint32
            actual=k.GetFileAttributesW(str(item))
            if actual==0xffffffff:raise ValueError('attributes')
            attrs=(attrs & ~0x40000)|actual
        if stat.S_ISLNK(info.st_mode) or attrs & 0x441400 or stat.S_ISREG(info.st_mode) and info.st_nlink!=1:
            raise ValueError('ordinary path required')
    return path
def read(path,maximum):
    plain(path);leaf=path.lstat()
    with path.open('rb') as handle:
        a=os.fstat(handle.fileno());data=handle.read(maximum+1);b=os.fstat(handle.fileno())
    plain(path);current=path.lstat()
    if not stat.S_ISREG(a.st_mode) or a.st_nlink!=1 or len(data)>maximum or (leaf.st_dev,leaf.st_ino)!=(a.st_dev,a.st_ino) or (a.st_dev,a.st_ino)!=(current.st_dev,current.st_ino) or (a.st_ino,a.st_mtime_ns,a.st_size)!=(b.st_ino,b.st_mtime_ns,b.st_size):raise ValueError('unstable metadata')
    result=json.loads(data.decode('utf-8-sig'))
    if not isinstance(result,dict):raise ValueError('object required')
    return result
def tail(path):
    empty={'available':False,'bytes':0,'truncated':False,'dataBase64':''}
    try:
        plain(path);leaf=path.lstat()
        with path.open('rb') as handle:
            a=os.fstat(handle.fileno())
            if not stat.S_ISREG(a.st_mode) or a.st_nlink!=1:raise ValueError('ordinary log required')
            handle.seek(max(0,a.st_size-32768));data=handle.read(min(a.st_size,32768));b=os.fstat(handle.fileno())
        plain(path);current=path.lstat()
        if (leaf.st_dev,leaf.st_ino)!=(a.st_dev,a.st_ino) or (a.st_dev,a.st_ino)!=(current.st_dev,current.st_ino) or a.st_ino!=b.st_ino or b.st_size<a.st_size:raise ValueError('log replaced or truncated')
        return {'available':True,'bytes':a.st_size,'truncated':a.st_size>len(data),'dataBase64':base64.b64encode(data).decode('ascii')}
    except (OSError,ValueError):return empty
def scheduler(ids):
    if not ids:return {}
    # Every interpolated token was independently restricted to 32 hex digits.
    script="$ErrorActionPreference='Stop'; $rows=@(); foreach($id in @("+','.join("'"+j+"'" for j in ids)+")){try{$t=Get-ScheduledTask -TaskPath '\\' -TaskName ('MSICodingJob-'+$id) -ErrorAction SilentlyContinue; if($null -eq $t){$rows+=@{id=$id;state='NotRegistered';result=$null}}else{$i=Get-ScheduledTaskInfo -TaskPath '\\' -TaskName $t.TaskName -ErrorAction Stop;$rows+=@{id=$id;state=[string]$t.State;result=$i.LastTaskResult}}}catch{$rows+=@{id=$id;state='Unknown';result=$null}}}; ConvertTo-Json -InputObject @($rows) -Compress"
    try:
        command=base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        result=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',command],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or len(result.stdout)>65536:return {}
        rows=json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(rows,list):return {}
        return {r['id']:(r['state'],r['result']) for r in rows if isinstance(r,dict) and r.get('id') in ids and isinstance(r.get('state'),str) and r['state'] in {'Unknown','NotRegistered','Disabled','Queued','Ready','Running'} and (r.get('result') is None or type(r['result']) is int)}
    except (OSError,ValueError,subprocess.TimeoutExpired):return {}
def when(value,optional=False):
    if optional and value is None:return
    if not isinstance(value,str) or len(value)>48:raise ValueError('time required')
    if datetime.datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:raise ValueError('timezone required')
def collect(root,query_id,scheduler_reader=scheduler):
    plain(root)
    if not root.is_dir():raise ValueError('jobs directory unavailable')
    paths=[];rejected=0;count=0;candidates=0
    # Keep a bounded candidate list and traversal count even on a damaged host.
    with os.scandir(root) as entries:
        for entry in entries:
            count+=1
            if count>10000:raise ValueError('job directory enumeration limit exceeded')
            if not ID.fullmatch(entry.name):continue
            candidates+=1
            try:
                path=pathlib.Path(entry.path);plain(path)
                if not path.is_dir():raise ValueError('directory required')
                paths.append((entry.stat(follow_symlinks=False).st_mtime_ns,path))
                if len(paths)>50:paths=sorted(paths,reverse=True)[:50]
            except (OSError,ValueError):rejected+=1
    selected=sorted(paths,reverse=True);jobs=[]
    for _,folder in selected:
        try:
            record=read(folder/'job.json',262144);status=read(folder/'status.json',65536);jid=folder.name;pid=record.get('projectId')
            expected_cache=r'C:\Users\jlewi\Documents\Codex\MSI-Working-Projects'+'\\'+str(pid)
            if record.get('format')!='msi-job-record-v1' or record.get('jobId')!=jid or record.get('catalogId')!='msi-project-library-v1' or not isinstance(pid,str) or not PID.fullmatch(pid) or DEVICE.fullmatch(pid) or record.get('taskName')!='MSICodingJob-'+jid or record.get('taskPath')!='\\' or str(record.get('cacheRoot','')).casefold()!=expected_cache.casefold():raise ValueError('record identity')
            if status.get('format')!='msi-job-status-v1' or status.get('jobId')!=jid or status.get('projectId')!=pid or not isinstance(status.get('status'),str) or status['status'] not in STATES:raise ValueError('status identity')
            when(record.get('createdUtc'));when(status.get('updatedUtc'));when(status.get('startedUtc'),True);when(status.get('finishedUtc'),True)
            code=status.get('exitCode')
            if code is not None and (type(code) is not int or not -(2**31)<=code<=2**32-1):raise ValueError('exit result')
            jobs.append({'jobId':jid,'projectId':pid,'status':status['status'],'exitCode':status.get('exitCode'),'createdUtc':record.get('createdUtc'),'startedUtc':status.get('startedUtc'),'updatedUtc':status.get('updatedUtc'),'finishedUtc':status.get('finishedUtc'),'taskState':'Unknown','lastTaskResult':None,'logs':{'stdout':tail(folder/'stdout.log'),'stderr':tail(folder/'stderr.log')}})
        except (OSError,ValueError,KeyError):rejected+=1
    tasks=scheduler_reader([row['jobId'] for row in jobs])
    for row in jobs:row['taskState'],row['lastTaskResult']=tasks.get(row['jobId'],('Unknown',None))
    return {'format':'interlinked-msi-jobs-v1','queryId':query_id,'host':'MSI','sid':SID,'observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'jobs':jobs,'truncated':candidates>50,'rejected':rejected}
'''


def remote_command(query_id):
    need(isinstance(query_id, str) and JOB_ID.fullmatch(query_id), 'Invalid job query ID.')
    source = REMOTE_READER + '\nverify_identity()\nprint(json.dumps(collect(ROOT,' + repr(query_id) + "),separators=(',',':')))\n"
    payload = base64.b64encode(zlib.compress(source.encode('utf-8'), 9)).decode('ascii')
    # Compression keeps this below cmd.exe's 8191-character remote-shell limit.
    # The quoted ASCII bootstrap has no cmd expansion or dynamic command input.
    bootstrap = "import base64,zlib;exec(zlib.decompress(base64.b64decode('" + payload + "')))"
    command = (r'C:\Users\jlewi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe '
               '-I -B -X utf8 -c "' + bootstrap + '"')
    need(len(command) <= 7500, 'MSI reader exceeds the remote shell command-line limit.')
    return command


def ssh_arguments(query_id, known_hosts):
    public = base64.b64decode(PUBLIC_KEY, validate=True)
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(public).digest()).decode('ascii').rstrip('=')
    need(fingerprint == FINGERPRINT, 'MSI public host pin is inconsistent.')
    options = ['-F', 'NUL', '-T']
    for option in ('HostName=192.168.247.1', 'HostKeyAlias=MSI',
                   'UserKnownHostsFile=' + str(known_hosts), 'GlobalKnownHostsFile=NUL',
                   'StrictHostKeyChecking=yes', 'VerifyHostKeyDNS=no', 'UpdateHostKeys=no',
                   'CheckHostIP=yes', 'HostKeyAlgorithms=ssh-ed25519',
                   'PreferredAuthentications=password', 'PasswordAuthentication=yes',
                   'PubkeyAuthentication=no', 'KbdInteractiveAuthentication=no',
                   'NumberOfPasswordPrompts=1', 'IdentityAgent=none', 'ForwardAgent=no',
                   'ClearAllForwardings=yes', 'ConnectionAttempts=1', 'ConnectTimeout=10',
                   'ServerAliveInterval=10', 'ServerAliveCountMax=3', 'LogLevel=ERROR'):
        options += ['-o', option]
    return [str(SYSTEM_SSH), *options, 'jlewi@MSI', remote_command(query_id)]


def native_query(query_id, run):
    """Worker console only: capture stdout, never stdin, stderr or a password."""
    known = run / 'known_hosts'
    ordinary(known, missing=True)
    with known.open('x', encoding='ascii', newline='\n') as stream:
        stream.write('MSI,192.168.247.1 ssh-ed25519 ' + PUBLIC_KEY + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    args = ssh_arguments(query_id, known)
    system_ssh(Path(args[0]))
    environment = os.environ.copy()
    for key in ('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY'):
        environment.pop(key, None)
    print('INTERLINKED — read-only MSI job refresh', flush=True)
    print('Enter the MSI Windows password only at the native SSH prompt below.', flush=True)
    print('No jobs will be started, cancelled or changed. This window closes after the query.', flush=True)
    # stdin and stderr inherit this dedicated interactive console. SSH reads its
    # password from the console; application code never reads or redirects it.
    process = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE, stderr=None,
                               cwd=str(run), env=environment, shell=False)
    chunks = []
    errors = []
    def drain():
        count = 0
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_RESPONSE:
                    errors.append('response_limit')
                    process.kill()  # only this read-only local SSH client
                    break
                chunks.append(chunk)
        except OSError:
            errors.append('read_error')
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=QUERY_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        reader.join(timeout=5)
        raise ValueError('MSI query timed out. Previous observations were retained.') from None
    reader.join(timeout=5)
    need(not reader.is_alive() and not errors, 'MSI query exceeded its bounded response.')
    need(code == 0, 'MSI SSH query did not complete. Previous observations were retained.')
    value = json.loads(b''.join(chunks).decode('utf-8-sig'))
    return validate_snapshot(value, query_id)


def state_owner(runtime):
    expected = dict(format='interlinked-gui-state-v1', kit=str(runtime.kit), demo=runtime.demo)
    need(read_json(Path(runtime.state) / 'OWNER.json', 8192) == expected,
         'Jobs state belongs to another GUI installation.')
    return dict(format='interlinked-jobs-owner-v1', guiOwner=expected)


class JobsMonitor:
    def __init__(self, runtime, transport=None, clock=time.time, launcher=subprocess.Popen):
        self.runtime = runtime
        self.clock = clock
        self.transport = transport
        self.launcher = launcher
        self.lock = threading.RLock()
        self.root = Path(runtime.state) / 'jobs'
        self.owner = state_owner(runtime)
        ordinary(self.root, missing=True)
        self.root.mkdir(exist_ok=True)
        marker = self.root / 'OWNER.json'
        if marker.exists():
            need(read_json(marker, 8192) == self.owner, 'Jobs state ownership differs.')
        else:
            need(not any(self.root.iterdir()), 'Existing jobs state has no ownership record.')
            write_json(marker, self.owner)
        self.snapshot = None
        self.received_at = None
        self.error = None
        self.session_observed = False
        self.refreshing = False
        self.closed = False
        cache = self.root / 'snapshot.json'
        if cache.exists():
            stored = read_json(cache)
            need(set(stored) == {'owner', 'receivedAt', 'snapshot'} and stored['owner'] == self.owner,
                 'Cached jobs observation ownership differs.')
            self.received_at = timestamp(stored['receivedAt'])
            self.snapshot = validate_snapshot(stored['snapshot'])
        if runtime.demo and self.snapshot is None:
            self._accept(demo_snapshot(), utc())

    def _check_owner(self):
        need(state_owner(self.runtime) == self.owner and read_json(self.root / 'OWNER.json', 8192) == self.owner,
             'Jobs state ownership changed.')

    def _accept(self, snapshot, received_at):
        validated = validate_snapshot(snapshot)
        received = timestamp(received_at)
        self._check_owner()
        write_json(self.root / 'snapshot.json', dict(owner=self.owner, receivedAt=received_at, snapshot=validated))
        self.snapshot = validated
        self.received_at = received
        self.session_observed = True
        self.error = None

    def view(self):
        with self.lock:
            stale = (not self.session_observed or self.snapshot is None or self.error is not None or self.received_at is None
                     or not 0 <= self.clock() - self.received_at <= STALE_SECONDS
                     or not -5 <= self.clock() - timestamp(self.snapshot['observedAt']) <= STALE_SECONDS)
            jobs = []
            if self.snapshot:
                for source in self.snapshot['jobs']:
                    row = {key: copy.deepcopy(value) for key, value in source.items() if key != 'logs'}
                    row['warning'] = warning_for(source)
                    row['logs'] = {name: {k: v for k, v in entry.items() if k != 'dataBase64'}
                                   for name, entry in source['logs'].items()}
                    jobs.append(row)
            ssh = 'connecting' if self.refreshing else 'unavailable' if self.error else 'not_checked' if stale else 'connected'
            message = ('DEMO: synthetic job observations only.' if self.runtime.demo else
                       'Authenticate in the native SSH window to refresh read-only records.' if self.refreshing else
                       'Read-only records received. Worker readiness has not been tested.' if not stale else
                       'Refresh explicitly to observe MSI. Cached records do not prove current job state.')
            return dict(readOnly=True, demo=self.runtime.demo, refreshing=self.refreshing,
                        connection=dict(ssh=ssh, worker='unknown', message=message),
                        observedAt=self.snapshot['observedAt'] if self.snapshot else None,
                        stale=stale, error=self.error, jobs=jobs,
                        truncated=self.snapshot['truncated'] if self.snapshot else False,
                        rejected=self.snapshot['rejected'] if self.snapshot else 0,
                        notice='Read-only observations. No job submission, cancellation, retry, cleanup or result import.')

    def refresh(self):
        with self.lock:
            need(not self.closed, 'Jobs monitor is closed.')
            need(not self.refreshing, 'A read-only MSI refresh is already in progress.')
            self._check_owner()
            self.refreshing = True
            self.error = None
            query_id = uuid.uuid4().hex
        def work():
            try:
                if self.runtime.demo:
                    snapshot = demo_snapshot(query_id)
                elif self.transport is not None:
                    snapshot = self.transport(query_id)
                else:
                    snapshot = self._launch(query_id)
                validate_snapshot(snapshot, query_id)
                with self.lock:
                    self._accept(snapshot, utc())
            except Exception:
                # Do not persist arbitrary subprocess/SSH text, command strings,
                # authentication errors or server paths in the app's snapshot.
                with self.lock:
                    self.error = 'MSI refresh could not be verified. Previous observations were retained; check the native SSH window and connection.'
            finally:
                with self.lock:
                    self.refreshing = False
        threading.Thread(target=work, daemon=True, name='interlinked-msi-read-only').start()
        return self.view()

    def _launch(self, query_id):
        need(os.name == 'nt' and os.environ.get('COMPUTERNAME', '').casefold() == 'this-machine',
             'MSI observations require the reviewed THIS-MACHINE client.')
        need(Path(self.runtime.kit) == KIT and Path(__file__).parent == KIT / 'INTERLINKED-App',
             'Use a reviewed deployed jobs monitor; the source checkout is not an installed kit.')
        python = ordinary(Path(self.runtime.python))
        run = self.root / ('query-' + query_id)
        ordinary(run, missing=True)
        run.mkdir()
        write_json(run / 'OWNER.json', dict(owner=self.owner, queryId=query_id))
        # A visible console is required solely for the user's native SSH prompt.
        process = self.launcher([str(python), '-I', '-B', str(Path(__file__)), '--worker', query_id],
                                cwd=str(run), shell=False, creationflags=subprocess.CREATE_NEW_CONSOLE)
        try:
            code = process.wait(timeout=QUERY_TIMEOUT + FAILURE_DISPLAY_SECONDS + 30)
        except subprocess.TimeoutExpired:
            process.terminate()  # local read-only helper only, never MSI jobs
            raise ValueError('Local MSI observation helper timed out.') from None
        need(code == 0, 'MSI read-only query helper did not complete.')
        self._check_owner()
        return validate_snapshot(read_json(run / 'result.json'), query_id)

    def log(self, job_id, stream):
        need(isinstance(job_id, str) and JOB_ID.fullmatch(job_id), 'Invalid job ID.')
        need(stream in {'stdout', 'stderr'}, 'Choose stdout or stderr.')
        with self.lock:
            need(self.snapshot is not None, 'No verified MSI job observations are available.')
            row = next((r for r in self.snapshot['jobs'] if r['jobId'] == job_id), None)
            need(row is not None, 'This job is not in the current observation.')
            entry = row['logs'][stream]
            text = base64.b64decode(entry['dataBase64']).decode('utf-8', 'replace')
            # The API serves plain text, with terminal and direction overrides
            # removed. The web client must render it with textContent.
            text = ''.join(c for c in text if c in '\n\r\t' or ord(c) >= 32 and ord(c) != 127
                           and not 0x202a <= ord(c) <= 0x202e and not 0x2066 <= ord(c) <= 0x2069)
            return dict(jobId=job_id, stream=stream, text=text, available=entry['available'],
                        bytes=entry['bytes'], truncated=entry['truncated'],
                        observedAt=self.snapshot['observedAt'], stale=self.view()['stale'], readOnly=True)

    def close(self):
        # Closing this view neither terminates SSH nor touches any remote jobs.
        with self.lock:
            self.closed = True


class UnavailableJobs:
    def __init__(self, demo=False):
        self.demo = demo

    def view(self):
        return dict(readOnly=True, demo=self.demo, refreshing=False,
                    connection=dict(ssh='unavailable', worker='unknown', message='Jobs observation state needs review; project workflows remain available.'),
                    observedAt=None, stale=True, error='Jobs observation ownership or cache could not be verified. Existing files were preserved.',
                    jobs=[], truncated=False, rejected=0, notice='Read-only MSI job observations are unavailable.')

    def refresh(self):
        raise ValueError('Jobs observation state needs review before refreshing.')

    def log(self, job_id, stream):
        raise ValueError('No verified job logs are available.')

    def close(self):
        pass


def safe_service(runtime):
    try:
        return JobsMonitor(runtime)
    except Exception:
        return UnavailableJobs(runtime.demo)


def demo_snapshot(query_id=None):
    now = utc()
    jobs = []
    for jid, pid, state, task, result, text in (
        ('a' * 32, 'demo-analysis', 'RUNNING', 'Running', None, 'DEMO analysis: processing synthetic input\nProgress 3 / 5\n'),
        ('b' * 32, 'demo-build', 'COMPLETED', 'Ready', 0, 'DEMO build completed\nAll synthetic checks passed\n'),
        ('c' * 32, 'demo-interrupted', 'RUNNING', 'Ready', 1, 'DEMO interrupted worker; review retained records\n')):
        data = text.encode('utf-8')
        jobs.append(dict(jobId=jid, projectId=pid, status=state,
                         exitCode=0 if state == 'COMPLETED' else None, createdUtc=now,
                         startedUtc=now, updatedUtc=now, finishedUtc=now if state == 'COMPLETED' else None,
                         taskState=task, lastTaskResult=result,
                         logs=dict(stdout=dict(available=True, bytes=len(data), truncated=False, dataBase64=base64.b64encode(data).decode('ascii')),
                                   stderr=dict(available=True, bytes=0, truncated=False, dataBase64=''))))
    return dict(format='interlinked-msi-jobs-v1', queryId=query_id or uuid.uuid4().hex,
                host='MSI', sid=MSI_SID, observedAt=now, jobs=jobs, truncated=False, rejected=0)


def worker(query_id):
    need(os.name == 'nt' and os.environ.get('COMPUTERNAME', '').casefold() == 'this-machine'
         and Path(__file__).parent == KIT / 'INTERLINKED-App', 'Use the reviewed installed MSI jobs helper.')
    need(JOB_ID.fullmatch(query_id), 'Invalid jobs query identity.')
    root = KIT / 'INTERLINKED-GUI-State' / 'jobs'
    run = ordinary(Path.cwd())
    need(run == root / ('query-' + query_id), 'Unexpected jobs query directory.')
    owner = dict(format='interlinked-jobs-owner-v1', guiOwner=dict(format='interlinked-gui-state-v1', kit=str(KIT), demo=False))
    need(read_json(root / 'OWNER.json', 8192) == owner
         and read_json(root.parent / 'OWNER.json', 8192) == owner['guiOwner']
         and read_json(run / 'OWNER.json', 8192) == dict(owner=owner, queryId=query_id),
         'Jobs query ownership differs.')
    snapshot = native_query(query_id, run)
    write_json(run / 'result.json', snapshot)


def failure_display():
    """Keep native errors visible briefly; never read/store SSH stderr or secrets."""
    print('MSI read-only refresh did not complete. Existing observations and all remote jobs were preserved.',
          file=sys.stderr, flush=True)
    if os.name != 'nt':
        return
    try:
        import msvcrt
        # The worker already owns a native console. Opening its output explicitly
        # keeps this prompt out of any parent-service stdout/stderr redirection.
        with open('CONOUT$', 'w', encoding='utf-8') as console:
            console.write('Review the native error above. Press Enter to close; this window closes automatically in '
                          + str(FAILURE_DISPLAY_SECONDS) + ' seconds.\n')
            console.flush()
            deadline = time.monotonic() + FAILURE_DISPLAY_SECONDS
            while time.monotonic() < deadline:
                if msvcrt.kbhit() and msvcrt.getwch() in ('\r', '\n'):
                    break
                time.sleep(.05)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description='Read-only MSI job observation helper.')
    parser.add_argument('--worker', required=True)
    args = parser.parse_args()
    try:
        worker(args.worker)
        return 0
    except Exception:
        failure_display()
        return 2


if __name__ == '__main__':
    sys.exit(main())
