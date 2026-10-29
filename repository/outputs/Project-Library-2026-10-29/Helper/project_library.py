#!/usr/bin/env python3
"""Windows project-library client. Python 3.12 standard library only.

All returned versions are immutable; only HEAD and owned cache state advance.
No credentials, share mounting, settings changes, application execution, or
automatic eviction. --test-local is exclusively for local fixture roots.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid

BLOCK=4*1024*1024
RESERVE=2*1024**3
DEVICE=re.compile(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z',re.I)
SHA=re.compile(r'[0-9a-f]{64}\Z')
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
VERSION=re.compile(r'[0-9a-f]{32}\Z')

class LibraryError(Exception):pass

def need(ok,message):
    if not ok:raise LibraryError(message)

def absolute(value):
    value=str(value);need(not value.startswith(('\\\\?\\','\\\\.\\')),'Device/extended input paths are forbidden')
    return Path(os.path.abspath(value))

def fs(path):
    value=str(path)
    if os.name!='nt' or value.startswith('\\\\?\\'):return value
    value=os.path.abspath(value)
    return '\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value

def exists(path):
    try:os.lstat(fs(path));return True
    except FileNotFoundError:return False

def check_path(path):
    leaf=None
    for item in (Path(path),*Path(path).parents):
        try:s=os.lstat(fs(item))
        except FileNotFoundError:continue
        if item==Path(path):leaf=s
        attrs=getattr(s,'st_file_attributes',0)
        need(not stat.S_ISLNK(s.st_mode) and not attrs&(0x400|0x1000|0x40000|0x400000),'Link/reparse/offline/recall path forbidden: '+str(item))
    return leaf

def regular(path):
    s=check_path(path);need(s is not None and stat.S_ISREG(s.st_mode),'Missing regular file: '+str(path));return s

def directory(path):
    s=check_path(path);need(s is not None and stat.S_ISDIR(s.st_mode),'Missing directory: '+str(path));return path

def relative(value):
    need(isinstance(value,str) and value,'Empty relative path')
    value=value.replace('\\','/');need(not value.startswith('/'),'Absolute relative path forbidden')
    for item in value.split('/'):
        need(item not in ('','.','..') and not item.endswith((' ','.')),'Unsafe path: '+value)
        need(not any(ord(c)<32 or c in '<>:"|?*' for c in item) and not DEVICE.fullmatch(item),'Windows filename/device alias forbidden: '+value)
    return value

def identifier(value):
    need(isinstance(value,str) and ID.fullmatch(value) is not None and not DEVICE.fullmatch(value),'Invalid project ID');return value

def paths_unique(values):
    files=set();dirs=set()
    for raw in values:
        path=relative(raw);key=path.casefold()
        need(key not in files and key not in dirs,'Duplicate/aliased file path: '+path)
        for n in range(1,len(path.split('/'))):
            parent='/'.join(path.split('/')[:n]).casefold();need(parent not in files,'File/directory collision: '+path);dirs.add(parent)
        files.add(key)

def join(root,value):
    value=relative(value);path=Path(root).joinpath(*value.split('/'));need(path.is_relative_to(root),'Path escaped its owned root');check_path(path);return path

def mkdir(path):check_path(path);os.makedirs(fs(path),exist_ok=True);directory(path)

def native():
    need(os.name=='nt','This client requires Windows')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=[ctypes.c_void_p];k.CloseHandle.restype=ctypes.c_int
    return k

def open_handle(path,access,share,creation=3,flags=0x08300000):
    check_path(path);k=native();h=k.CreateFileW(fs(path),access,share,None,creation,flags,None)
    if h==ctypes.c_void_p(-1).value:
        code=ctypes.get_last_error()
        if code in (32,33):raise LibraryError('A file/project is in use. Close its applications or let the other operation finish: '+str(path))
        raise ctypes.WinError(code)
    return h

@contextlib.contextmanager
def reader(path,live=False,delete=False):
    regular(path);h=open_handle(path,0x80000000|(0x10000 if delete else 0),0 if delete else 7 if live else 1)
    try:
        stream=NativeReader(h);fingerprint(stream);yield stream
    finally:native().CloseHandle(h)

class NativeReader:
    """Read/seek on an owned Win32 HANDLE, without a CRT file descriptor.

    Thousands of held locks must not exhaust the Windows CRT's 8,192-fd table.
    No read cache is retained, so active JSONL rechecks reread actual bytes.
    """
    def __init__(self,handle):self.handle=handle
    def read(self,size=-1):
        if size is None or size<0:
            chunks=[]
            while True:
                chunk=self.read(BLOCK)
                if not chunk:return b''.join(chunks)
                chunks.append(chunk)
        if size==0:return b''
        need(0<size<=64*1024*1024,'Native read request exceeds bounded buffer')
        k=native();k.ReadFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.c_void_p];k.ReadFile.restype=ctypes.c_int
        buffer=ctypes.create_string_buffer(size);count=ctypes.c_uint32()
        if not k.ReadFile(self.handle,buffer,size,ctypes.byref(count),None):raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw[:count.value]
    def seek(self,offset,whence=0):
        need(whence in (0,1,2),'Invalid seek mode');k=native()
        k.SetFilePointerEx.argtypes=[ctypes.c_void_p,ctypes.c_longlong,ctypes.POINTER(ctypes.c_longlong),ctypes.c_uint32];k.SetFilePointerEx.restype=ctypes.c_int
        position=ctypes.c_longlong()
        if not k.SetFilePointerEx(self.handle,offset,ctypes.byref(position),whence):raise ctypes.WinError(ctypes.get_last_error())
        return position.value

@contextlib.contextmanager
def exclusive(path,create=False):
    if not create:regular(path)
    h=open_handle(path,0xC0000000 if create else 0x80000000,0,4 if create else 3,0x00300000)
    try:yield
    finally:native().CloseHandle(h)

@contextlib.contextmanager
def directory_guard(path):
    directory(path);h=open_handle(path,0x80000000,3,3,0x02300000)
    try:yield
    finally:native().CloseHandle(h)

def fingerprint(stream):
    class INFO(ctypes.Structure):
        _pack_=4
        _fields_=[('attrs',ctypes.c_uint32),('created',ctypes.c_uint64),('accessed',ctypes.c_uint64),('written',ctypes.c_uint64),('volume',ctypes.c_uint32),('hiSize',ctypes.c_uint32),('loSize',ctypes.c_uint32),('links',ctypes.c_uint32),('hiId',ctypes.c_uint32),('loId',ctypes.c_uint32)]
    k=native();k.GetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.POINTER(INFO)];k.GetFileInformationByHandle.restype=ctypes.c_int
    info=INFO()
    if not k.GetFileInformationByHandle(stream.handle,ctypes.byref(info)):raise ctypes.WinError(ctypes.get_last_error())
    need(not info.attrs&(0x400|0x1000|0x40000|0x400000),'Handle is a reparse/offline/recall file')
    return {'fileId':(info.hiId<<32)|info.loId,'volume':info.volume,'bytes':(info.hiSize<<32)|info.loSize,'mtime_ns':(info.written-116444736000000000)*100}

def delete_held(stream):
    k=native();k.SetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];k.SetFileInformationByHandle.restype=ctypes.c_int
    flag=ctypes.c_int(1)
    if not k.SetFileInformationByHandle(stream.handle,4,ctypes.byref(flag),ctypes.sizeof(flag)):raise ctypes.WinError(ctypes.get_last_error())

def unique_keys(pairs):
    result={}
    for key,value in pairs:need(key not in result,'Duplicate JSON key');result[key]=value
    return result

def read_json(path):
    with reader(path) as stream:
        need(fingerprint(stream)['bytes']<=256*1024*1024,'Metadata file exceeds 256 MiB limit')
        value=json.loads(stream.read().decode('utf-8-sig'),object_pairs_hook=unique_keys)
        need(isinstance(value,dict),'Metadata must be a JSON object: '+str(path));return value

def atomic_json(path,value,replace=True):
    check_path(path);directory(Path(path).parent);temp=Path(path).with_name(Path(path).name+'.tmp-'+uuid.uuid4().hex)
    with open(fs(temp),'x',encoding='utf-8',newline='\n') as out:
        json.dump(value,out,ensure_ascii=False,separators=(',',':'));out.write('\n');out.flush();os.fsync(out.fileno())
    if replace:os.replace(fs(temp),fs(path))
    else:os.rename(fs(temp),fs(path))

class Progress:
    def __init__(self):self.last=0;self.files=0
    def tick(self,phase,path='',done=0,total=0,force=False):
        now=time.monotonic()
        if force or now-self.last>=5:
            print(f'{phase}: {path} | {done:,}/{total:,} bytes | {self.files:,} files processed',file=sys.stderr,flush=True);self.last=now

def digest(stream,length=None,progress=None,label=''):
    stream.seek(0);h=hashlib.sha256();size=0
    while length is None or size<length:
        block=stream.read(BLOCK if length is None else min(BLOCK,length-size))
        if not block:break
        h.update(block);size+=len(block)
        if progress:progress.tick('Verifying',label,size,length or 0)
    need(length is None or size==length,'File shortened during verification: '+label)
    return h.hexdigest(),size

def file_digest(path):
    with reader(path) as stream:return digest(stream)[0]

def space(path,additional=0):
    free=shutil.disk_usage(fs(path)).free
    need(free>=RESERVE+additional,f'Insufficient free space at {path}: {free:,} bytes free; need {additional:,} bytes plus 2 GiB reserve')
    return free

def prefix_length(stream,size):
    end=size
    while end:
        start=max(0,end-BLOCK);stream.seek(start);data=stream.read(end-start);index=data.rfind(b'\n')
        if index>=0:return start+index+1
        end=start
    return 0

def scan(root):
    directory(root);files={};directories=[];todo=[('',Path(root))]
    while todo:
        rel,folder=todo.pop();directory(folder)
        with os.scandir(fs(folder)) as entries:
            for entry in entries:
                name=relative((rel+'/' if rel else '')+entry.name);path=join(root,name);s=check_path(path)
                need(s is not None,'Directory changed while scanning')
                if stat.S_ISDIR(s.st_mode):directories.append(name);todo.append((name,path))
                else:need(stat.S_ISREG(s.st_mode),'Unsupported local filesystem object: '+name);files[name]=path
    paths_unique(files);return files,sorted(directories)

def validate_record(record,hashed=True):
    relative(record['path']);need(type(record['bytes']) is int and record['bytes']>=0 and type(record['mtime_ns']) is int,'Invalid file metadata')
    if hashed:need(isinstance(record.get('sha256'),str) and SHA.fullmatch(record['sha256']) is not None,'Invalid file SHA256')

class Client:
    def __init__(self,args):
        self.library=absolute(args.library);self.returns=absolute(args.returns);self.cache=absolute(args.cache);self.progress=Progress();self.test=args.test_local
        if not self.test:
            need(str(self.library).startswith('\\\\') and str(self.returns).startswith('\\\\'),'Library and returns must be UNC shares')
        need(not str(self.cache).startswith('\\\\') and self.cache!=Path(self.cache.anchor),'Cache must be a local non-root folder')
        roots=[str(p).rstrip('\\/').casefold() for p in (self.library,self.returns,self.cache)]
        for i,a in enumerate(roots):
            for b in roots[i+1:]:need(a!=b and not a.startswith(b+'\\') and not b.startswith(a+'\\'),'Library, returns, and cache roots must be separate')
        self.catalog=None;self.project=None;self.root=None;self.state=None;self.source_refs=set()

    @contextlib.contextmanager
    def library_guard(self):
        directory(self.library)
        with reader(self.library/'.library-access.lock'):
            self.catalog=read_json(self.library/'catalog.json');need(self.catalog.get('format')=='project-library-catalog-v1','Unknown catalog format')
            need(isinstance(self.catalog.get('catalogId'),str) and self.catalog['catalogId'],'Missing catalog ID')
            need(isinstance(self.catalog.get('projects'),list),'Invalid projects list')
            ids=set()
            for project in self.catalog['projects']:
                pid=identifier(project['id']);need(pid.casefold() not in ids,'Duplicate project ID');ids.add(pid.casefold())
                need(isinstance(project.get('files'),list),'Invalid project files')
                paths_unique(row['path'] for row in project['files'])
                for row in project['files']:
                    validate_record(row,hashed=False);source=relative(row['sourceRelative']);need(source.startswith('Sources/'),'Source reference must be inside Sources/')
                    need(row.get('kind') in ('file','sqlite_snapshot','jsonl'),'Unknown source capture kind')
            yield

    def choose(self,pid):
        identifier(pid);matches=[p for p in self.catalog['projects'] if p['id']==pid];need(len(matches)==1,'Project ID is absent from catalog');self.project=matches[0]
        self.source_refs={relative(r['sourceRelative']) for r in self.project['files']};return self.project

    def identity(self,pid):
        return {'projectId':pid,'catalogId':self.catalog['catalogId'],'library':str(self.library),'returns':str(self.returns)}

    def check_owner(self,marker,expected):
        data=read_json(marker);need(data.get('format')=='project-library-owned-v1','Invalid ownership marker')
        for key,value in expected.items():need(data.get(key)==value,'Ownership mismatch: '+key)

    def cache_root(self,pid,create=False):
        identifier(pid);self.root=join(self.cache,pid)
        if not exists(self.root):
            need(create,'Project is not cached');mkdir(self.cache);os.mkdir(fs(self.root))
            atomic_json(self.root/'OWNER.json',dict(format='project-library-owned-v1',**self.identity(pid)),replace=False)
        self.check_owner(self.root/'OWNER.json',self.identity(pid));directory(self.root)
        if create:mkdir(self.root/'Files');mkdir(self.root/'Partials')
        return self.root

    def remote_root(self,create=False):
        directory(self.returns);root=join(self.returns,self.project['id'])
        if not exists(root):
            if not create:return root
            os.mkdir(fs(root));atomic_json(root/'OWNER.json',dict(format='project-library-owned-v1',**self.identity(self.project['id'])),replace=False)
        self.check_owner(root/'OWNER.json',self.identity(self.project['id']));return root

    @contextlib.contextmanager
    def returns_guard(self,write=False):
        root=self.remote_root(create=write)
        if write:
            with exclusive(root/'.project.lock',create=True):yield root
        elif exists(root/'.project.lock'):
            with reader(root/'.project.lock'):yield root
        else:yield root

    def read_head(self):
        root=self.remote_root();path=root/'HEAD.json'
        if not exists(path):return None
        head=read_json(path);self.validate_head(head);return head

    def validate_head(self,head):
        need(isinstance(head,dict) and head.get('format')=='project-library-head-v1','Invalid HEAD format')
        need(head.get('projectId')==self.project['id'] and head.get('catalogId')==self.catalog['catalogId'],'HEAD project/catalog conflict')
        need(isinstance(head.get('versionId'),str) and VERSION.fullmatch(head['versionId']) is not None and SHA.fullmatch(head.get('manifestSha256','')) is not None,'Invalid HEAD reference')

    def validate_ref(self,record):
        ref=record['ref'];path=relative(ref['path']);store=ref.get('store')
        if store=='library':
            need(path.startswith('Sources/'),'Library reference escaped Sources/')
            need(path in self.source_refs,'Library reference is outside the selected project')
        elif store=='returns':
            pieces=path.split('/');need(len(pieces)>=5 and pieces[0]==self.project['id'] and pieces[1]=='versions' and VERSION.fullmatch(pieces[2]) is not None and pieces[3]=='Payload','Return reference escaped its project/version payload')
        else:raise LibraryError('Invalid reference store')
        need(ref.get('method') in ('file','jsonl_prefix'),'Invalid reference capture method')
        return join(self.library if store=='library' else self.returns,path)

    def version(self,head):
        self.validate_head(head)
        path=self.remote_root()/'versions'/head['versionId']/'manifest.json';need(file_digest(path)==head['manifestSha256'],'Version manifest SHA256 mismatch')
        version=read_json(path)
        need(version.get('format')=='project-library-version-v1' and version.get('versionId')==head['versionId'] and version.get('projectId')==self.project['id'] and version.get('catalogId')==self.catalog['catalogId'],'Version identity mismatch')
        self.validate_originals(version['originals']);paths_unique(r['path'] for r in version['files'])
        for record in version['files']:validate_record(record);self.validate_ref(record)
        for path in version.get('directories',[]):relative(path)
        return version

    def validate_originals(self,originals):
        paths_unique(r['path'] for r in originals)
        selected={r['path']:r for r in self.project['files']};need(set(selected)=={r['path'] for r in originals},'Original baseline does not match catalog membership')
        for record in originals:
            validate_record(record);ref=record['ref'];row=selected[record['path']]
            need(ref.get('store')=='library' and ref.get('path')==row['sourceRelative'],'Original baseline reference differs from catalog')
            need(ref.get('method')==('jsonl_prefix' if row['kind']=='jsonl' else 'file'),'Original snapshot kind mismatch');self.validate_ref(record)

    def load_state(self):
        state=read_json(self.root/'state.json');need(state.get('format')=='project-library-cache-v1','Invalid cache state')
        for key,value in self.identity(self.project['id']).items():need(state.get(key)==value,'Cache state identity mismatch')
        paths_unique(r['path'] for r in state['files'])
        for row in state['files']:
            validate_record(row,hashed=state['status']!='FETCHING' or row.get('sha256') is not None);self.validate_ref(row)
        self.state=state;return state

    def save_state(self,state):atomic_json(self.root/'state.json',state);self.state=state

    def event(self,kind,**data):
        path=self.root/'events.jsonl';check_path(path)
        with open(fs(path),'a',encoding='utf-8',newline='\n') as out:
            out.write(json.dumps(dict(event=kind,time=time.time(),**data),ensure_ascii=False,separators=(',',':'))+'\n');out.flush();os.fsync(out.fileno())

    def summary(self,**values):
        return dict(projectId=self.project['id'],workspacePath=str(self.root/'Files') if self.root else None,**values)

    def resolve_pending(self,state,write=False):
        pending=state.get('pendingSave')
        if pending is None:return state,False
        need(isinstance(pending,dict) and pending.get('parentHead')==state['baselineHead'],'Invalid pending-save baseline')
        intended=pending['head'];current=self.read_head()
        need(current in (pending['parentHead'],intended),'Remote HEAD conflict with pending save')
        need(current==intended or write,'A verified save was interrupted before HEAD publication. Rerun Save to reverify and finish it.')
        version=self.version(intended)
        need(version.get('parentHead')==pending['parentHead'] and version['originals']==state['originals'],'Pending version differs from original baseline')
        with contextlib.ExitStack() as stack:
            refs=self.verified_refs(version['originals']+version['files'],stack);local,dirs=self.local_held(stack)
            self.matching_saved(local,{'files':version['files']});need(dirs==version.get('directories',[]),'Local directory membership changed since pending save')
            self.check_local(local,dirs);self.recheck_refs(refs);need(self.read_head()==current,'Remote HEAD changed during save recovery')
            if current!=intended:atomic_json(self.remote_root()/'HEAD.json',intended)
            state.update(baselineHead=intended,savedHead=intended,files=version['files'],directories=version.get('directories',[]),status='READY',savedUnixTime=time.time())
            state.pop('pendingSave',None);self.save_state(state);self.event('SAVE_RECOVERED',head=intended)
        return state,True

    def list(self):
        with self.library_guard():
            return {'catalogId':self.catalog['catalogId'],'projects':[{'id':p['id'],'name':p.get('name',p['id']),'description':p.get('description',''),'files':len(p['files']),'bytes':sum(r['bytes'] for r in p['files']),'warnings':p.get('warnings',[]),'notes':p.get('notes',[]),**{key:p.get(key) for key in ('workspaceRelative','workspacePaths','environmentHints','entryPoints') if key in p}} for p in self.catalog['projects']]}

    def read_ref(self,record,stack):
        # Verification/save/release retain stable read locks on every reference.
        # Active JSONL prefix capture is supported only by fetch; a writer must
        # close before its original can serve as a safe release prerequisite.
        path=self.validate_ref(record);stream=stack.enter_context(reader(path));before=fingerprint(stream)
        need(before['bytes']>=record['bytes'] if record['ref']['method']=='jsonl_prefix' else before['bytes']==record['bytes'],'Remote reference length changed: '+record['path'])
        value=digest(stream,record['bytes'],self.progress,record['path'])[0];need(value==record['sha256'],'Remote reference content changed: '+record['path'])
        if record['ref']['method']=='file':need(fingerprint(stream)==before,'Remote reference changed while reading')
        return stream,before

    def verified_refs(self,records,stack,existing=None):
        held=dict(existing or {})
        for record in records:
            key=(record['ref']['store'],record['ref']['path'],record['bytes'],record['sha256'],record['ref']['method'])
            if key not in held:held[key]=(record,*self.read_ref(record,stack))
        return held

    def recheck_refs(self,held):
        for record,stream,before in held.values():
            now=fingerprint(stream)
            if record['ref']['method']=='file':need(now==before,'Remote source changed during operation')
            else:
                need(now['fileId']==before['fileId'] and now['volume']==before['volume'] and now['bytes']>=record['bytes'],'JSONL source identity/length changed')
                need(digest(stream,record['bytes'])[0]==record['sha256'],'JSONL original prefix changed')

    def initial_records(self):
        return [dict(path=r['path'],bytes=r['bytes'],mtime_ns=r['mtime_ns'],sha256=None,ref={'store':'library','path':r['sourceRelative'],'method':'jsonl_prefix' if r['kind']=='jsonl' else 'file'}) for r in self.project['files']]

    def partial_paths(self,record):
        # Distinct immutable returned versions must never adopt old partials.
        key=hashlib.sha256(json.dumps([record['path'],record['ref'],record.get('sha256')],sort_keys=True).encode()).hexdigest()
        return self.root/'Partials'/(key+'.part'),self.root/'Partials'/(key+'.json')

    def fetch_remaining(self,records):
        remaining=0
        for record in records:
            if exists(join(self.root/'Files',record['path'])):continue
            amount=record['bytes'];part,pointer=self.partial_paths(record)
            if exists(pointer):
                meta=read_json(pointer);need(meta['path']==record['path'] and meta['ref']==record['ref'],'Unrelated partial metadata')
                amount=meta['length']
                if exists(part):
                    size=regular(part).st_size;need(size<=amount,'Partial exceeds recorded snapshot length');amount-=size
            remaining+=amount
        return remaining

    def copy_fetch(self,record):
        sourcepath=self.validate_ref(record);target=join(self.root/'Files',record['path']);live=record['ref']['method']=='jsonl_prefix'
        with reader(sourcepath,live=live) as source:
            before=fingerprint(source);length=record['bytes'] if record.get('sha256') else prefix_length(source,before['bytes']) if live else before['bytes']
            need(not live or before['bytes']==0 or length>0,'JSONL source has no complete newline-terminated record')
            need(before['bytes']>=length if live else before['bytes']==length,'Source length differs from captured reference')
            record=dict(record,bytes=length,mtime_ns=record['mtime_ns'] if record.get('sha256') else before['mtime_ns'])
            if exists(target):
                expected=digest(source,length,self.progress,record['path'])[0]
                need(record.get('sha256') in (None,expected),'Remote source differs from expected snapshot')
                need(regular(target).st_size==length and file_digest(target)==expected,'Existing local file differs; preserving local edits: '+record['path'])
            else:
                part,pointer=self.partial_paths(record)
                meta={'path':record['path'],'ref':record['ref'],'identity':before,'length':length,'expectedSha256':record.get('sha256')}
                if exists(pointer):
                    prior=read_json(pointer)
                    need(prior['path']==record['path'] and prior['ref']==record['ref'],'Unrelated partial metadata')
                    same=prior['identity']==before
                    if live:same=prior['identity']['fileId']==before['fileId'] and prior['identity']['volume']==before['volume'] and before['bytes']>=prior['length']
                    need(same,'Source changed since interrupted fetch; partial preserved for review')
                    length=prior['length'];record['bytes']=length
                else:
                    need(not exists(part),'Unowned partial file');atomic_json(pointer,meta,replace=False)
                offset=regular(part).st_size if exists(part) else 0;need(offset<=length,'Partial exceeds source snapshot length')
                source.seek(0);h=hashlib.sha256()
                if offset:
                    with reader(part) as saved:
                        left=offset
                        while left:
                            expectedblock=source.read(min(BLOCK,left));actual=saved.read(len(expectedblock));need(expectedblock and actual==expectedblock,'Partial prefix differs from current source');h.update(expectedblock);left-=len(expectedblock)
                space(self.root,length-offset)
                with open(fs(part),'ab' if exists(part) else 'xb') as out:
                    while offset<length:
                        space(self.root,min(BLOCK,length-offset));block=source.read(min(BLOCK,length-offset));need(block,'Source shortened during fetch');out.write(block);h.update(block);offset+=len(block);self.progress.tick('Fetching',record['path'],offset,length)
                    out.flush();os.fsync(out.fileno())
                expected=h.hexdigest();need(record.get('sha256') in (None,expected),'Fetched reference SHA256 mismatch')
                self.recheck_fetch(source,before,live,length,expected)
                mkdir(target.parent);need(not exists(target),'Local destination appeared; refusing overwrite');os.rename(fs(part),fs(target))
                need(regular(target).st_size==length and file_digest(target)==expected,'Local readback SHA256 mismatch')
                os.utime(fs(target),ns=(record['mtime_ns'],record['mtime_ns']))
            self.recheck_fetch(source,before,live,length,expected)
            self.progress.files+=1;return dict(record,sha256=expected)

    def recheck_fetch(self,source,before,live,length,expected):
        now=fingerprint(source)
        if live:
            need(now['fileId']==before['fileId'] and now['volume']==before['volume'] and now['bytes']>=length,'JSONL source changed identity/length')
            need(digest(source,length)[0]==expected,'JSONL prefix changed during fetch')
        else:need(now==before,'Source changed during fetch')

    def fetch(self,pid):
        with self.library_guard():
            self.choose(pid);self.cache_root(pid,create=True)
            with exclusive(self.root/'.client.lock',create=True),self.returns_guard():
                head=self.read_head();version=self.version(head) if head else None
                if exists(self.root/'state.json'):
                    state=self.load_state();state,_=self.resolve_pending(state)
                    if state['status']=='RELEASED':
                        need(not scan(self.root/'Files')[0],'Released workspace contains new files; preserving them');state=None
                    else:need(state['baselineHead']==head,'Remote HEAD changed; preserve/save this cache or use a separate cache root')
                else:
                    need(not scan(self.root/'Files')[0],'Owned cache has files without state; refusing adoption');state=None
                if state is None:
                    state=dict(format='project-library-cache-v1',**self.identity(pid),status='FETCHING',baselineHead=head,savedHead=head,files=version['files'] if version else self.initial_records(),originals=version['originals'] if version else [],directories=version.get('directories',[]) if version else [],startedUnixTime=time.time())
                    self.save_state(state)
                remaining=self.fetch_remaining(state['files'])
                free=space(self.root,remaining);self.progress.tick('Fetch preview',f"{len(state['files']):,} files; {remaining:,} bytes estimated; {free:,} bytes free",force=True)
                for i,record in enumerate(state['files']):
                    state['files'][i]=self.copy_fetch(record)
                    if (i+1)%25==0:self.save_state(state)
                for path in state.get('directories',[]):mkdir(join(self.root/'Files',path))
                if head is None:state['originals']=[dict(r) for r in state['files']]
                self.validate_originals(state['originals']);need(self.read_head()==head,'Remote HEAD changed during fetch; cache preserved for review')
                state['status']='READY';state['completedUnixTime']=time.time();self.save_state(state);self.event('FETCH_VERIFIED',head=head,files=len(state['files']))
                return self.summary(status='FETCH_VERIFIED',files=len(state['files']),bytes=sum(r['bytes'] for r in state['files']),head=head,notes=self.project.get('notes',[]),warnings=self.project.get('warnings',[]))

    def local_held(self,stack,delete=False):
        files,dirs=scan(self.root/'Files');held={}
        if delete:
            for path in [self.cache,self.root,self.root/'Files',*(join(self.root/'Files',p) for p in dirs)]:stack.enter_context(directory_guard(path))
        for name,path in sorted(files.items()):
            stream=stack.enter_context(reader(path,delete=delete));before=fingerprint(stream);value,size=digest(stream,progress=self.progress,label=name);need(fingerprint(stream)==before,'Local file changed while reading')
            held[name]={'stream':stream,'identity':before,'path':name,'bytes':size,'sha256':value,'mtime_ns':before['mtime_ns']};self.progress.files+=1
        return held,dirs

    def check_local(self,held,dirs):
        current,currentdirs=scan(self.root/'Files');need(set(current)==set(held) and currentdirs==dirs,'Local membership changed during operation')
        for value in held.values():need(fingerprint(value['stream'])==value['identity'],'Local file changed during operation')

    def matching_saved(self,held,state,allow_missing=False):
        expected={r['path']:r for r in state['files']}
        need(set(held)<=set(expected) if allow_missing else set(held)==set(expected),'Local files were added/deleted after the saved snapshot; save again first')
        for path,item in held.items():need(item['bytes']==expected[path]['bytes'] and item['sha256']==expected[path]['sha256'],'Local file changed since saved snapshot: '+path)

    def save(self,pid):
        with self.library_guard():
            self.choose(pid);self.cache_root(pid)
            with exclusive(self.root/'.client.lock',create=True),self.returns_guard(write=True) as remote,contextlib.ExitStack() as stack:
                state=self.load_state();state,recovered=self.resolve_pending(state,write=True)
                if recovered:return self.summary(status='SAVE_COMMITTED',head=state['savedHead'],files=len(state['files']),payloadBytes=0,recoveredInterruptedSave=True)
                need(state['status']=='READY','Fetch must finish before saving');self.validate_originals(state['originals']);head=self.read_head();need(head==state['baselineHead'],'Remote HEAD conflict. No merge or overwrite was performed.')
                refs=self.verified_refs(state['originals']+state['files'],stack);local,dirs=self.local_held(stack)
                prior={r['path']:r for r in state['files']};original={r['path']:r for r in state['originals']};plan=[];changed=0
                versionid=uuid.uuid4().hex
                for path,item in sorted(local.items()):
                    base=prior.get(path);orig=original.get(path);same=lambda r:r is not None and r['bytes']==item['bytes'] and r['sha256']==item['sha256']
                    ref=(base if same(base) else orig if same(orig) else {}).get('ref')
                    if ref is None:ref={'store':'returns','path':f'{pid}/versions/{versionid}/Payload/{path}','method':'file'};changed+=item['bytes']
                    plan.append(dict(path=path,bytes=item['bytes'],sha256=item['sha256'],mtime_ns=item['mtime_ns'],ref=ref))
                free=space(remote,changed);self.progress.tick('Save preview',f'{len(plan):,} files; {changed:,} new payload bytes; {free:,} bytes free',force=True)
                versions=remote/'versions';mkdir(versions);versionroot=versions/versionid;os.mkdir(fs(versionroot));mkdir(versionroot/'Payload')
                atomic_json(versionroot/'VERSION-OWNER.json',dict(format='project-library-version-owner-v1',projectId=pid,versionId=versionid,createdUnixTime=time.time()),replace=False)
                self.event('SAVE_STARTED',versionId=versionid,parentHead=head)
                for record in plan:
                    if record['ref']['path'].startswith(f'{pid}/versions/{versionid}/Payload/') and record['ref']['store']=='returns':
                        target=join(self.returns,record['ref']['path']);mkdir(target.parent);source=local[record['path']]['stream'];source.seek(0);done=0
                        with open(fs(target),'xb') as out:
                            while done<record['bytes']:
                                space(remote,min(BLOCK,record['bytes']-done));block=source.read(min(BLOCK,record['bytes']-done));need(block,'Local source shortened');out.write(block);done+=len(block);self.progress.tick('Saving',record['path'],done,record['bytes'])
                            out.flush();os.fsync(out.fileno())
                        need(regular(target).st_size==record['bytes'] and file_digest(target)==record['sha256'],'Saved payload readback mismatch')
                self.check_local(local,dirs);self.recheck_refs(refs);need(self.read_head()==head,'Remote HEAD changed before commit')
                version=dict(format='project-library-version-v1',projectId=pid,catalogId=self.catalog['catalogId'],versionId=versionid,parentHead=head,createdUnixTime=time.time(),originals=state['originals'],files=plan,directories=dirs,tombstones=sorted((set(original)|set(prior))-set(local)),notes=self.project.get('notes',[]),warnings=self.project.get('warnings',[]))
                atomic_json(versionroot/'manifest.json',version,replace=False);newhead={'format':'project-library-head-v1','projectId':pid,'catalogId':self.catalog['catalogId'],'versionId':versionid,'manifestSha256':file_digest(versionroot/'manifest.json')}
                # Verify every newly stored/reference payload under held read locks
                # before HEAD can make this version visible to other clients.
                newrefs=self.verified_refs(plan,stack,existing=refs);self.check_local(local,dirs);self.recheck_refs(newrefs);need(self.read_head()==head,'Remote HEAD changed before atomic commit')
                state['pendingSave']={'head':newhead,'parentHead':head};self.save_state(state)
                atomic_json(remote/'HEAD.json',newhead)
                state.update(baselineHead=newhead,savedHead=newhead,files=plan,directories=dirs,savedUnixTime=time.time(),status='READY');state.pop('pendingSave',None);self.save_state(state);self.event('SAVE_COMMITTED',head=newhead,files=len(plan),payloadBytes=changed)
                return self.summary(status='SAVE_COMMITTED',head=newhead,files=len(plan),payloadBytes=changed,versionPath=str(versionroot),tombstones=version['tombstones'])

    def verify(self,pid):
        with self.library_guard():
            self.choose(pid);self.cache_root(pid)
            with exclusive(self.root/'.client.lock',create=True),self.returns_guard(),contextlib.ExitStack() as stack:
                state=self.load_state();state,_=self.resolve_pending(state);need(state['status']=='READY','Project cache is not ready');self.validate_originals(state['originals']);need(self.read_head()==state['baselineHead'],'Remote HEAD changed since this cache snapshot')
                refs=self.verified_refs(state['originals']+state['files'],stack);local,dirs=self.local_held(stack);self.matching_saved(local,state);self.check_local(local,dirs);self.recheck_refs(refs)
                self.event('VERIFY_PASSED',head=state['baselineHead']);return self.summary(status='VERIFIED',files=len(local),head=state['baselineHead'],committedSave=state.get('savedHead') is not None)

    def release(self,pid,confirm):
        need(confirm==pid,'Release requires --confirm followed by the exact project ID; close all project applications first')
        with self.library_guard():
            self.choose(pid);self.cache_root(pid)
            with exclusive(self.root/'.client.lock',create=True),self.returns_guard():
                state=self.load_state();state,_=self.resolve_pending(state);need(state['status'] in ('READY','RELEASING','RELEASE_PARTIAL') and state.get('savedHead') is not None,'A complete committed save is required before release')
                resuming=state['status'] in ('RELEASING','RELEASE_PARTIAL')
                head=self.read_head();need(head==state['savedHead']==state['baselineHead'],'Remote HEAD changed; refusing release');version=self.version(head);need(version['files']==state['files'] and version['originals']==state['originals'],'Cache state differs from committed version')
                deletion_started=False
                try:
                    with contextlib.ExitStack() as stack:
                        refs=self.verified_refs(state['originals']+version['files'],stack);local,dirs=self.local_held(stack,delete=True);self.matching_saved(local,state,allow_missing=resuming);self.check_local(local,dirs);self.recheck_refs(refs);need(self.read_head()==head,'Remote HEAD changed before release')
                        self.event('RELEASE_VERIFIED',head=head,files=len(local),bytes=sum(v['bytes'] for v in local.values()))
                        state['status']='RELEASING';self.save_state(state)
                        self.check_local(local,dirs)
                        # Each handle identifies a previously verified regular file
                        # within Files. No recursive or path-based deletion is used.
                        for item in local.values():delete_held(item['stream']);deletion_started=True
                    remaining,_=scan(self.root/'Files');need(not remaining,'New local files appeared during release; those files were preserved')
                    state['status']='RELEASED';state['releasedUnixTime']=time.time();self.save_state(state);self.event('RELEASE_COMPLETE',head=head,files=len(local))
                    return self.summary(status='RELEASED',files=len(local),freedBytes=sum(v['bytes'] for v in local.values()),head=head)
                except BaseException:
                    if deletion_started:
                        state['status']='RELEASE_PARTIAL';self.save_state(state);self.event('RELEASE_PARTIAL',head=head,message='Some verified files were released; remaining files are preserved. Rerun release to reverify the full remote version and every remaining local file before continuing.')
                    raise

    def status(self,pid):
        identifier(pid);self.root=join(self.cache,pid);marker=read_json(self.root/'OWNER.json')
        self.check_owner(self.root/'OWNER.json',{'projectId':pid,'library':str(self.library),'returns':str(self.returns)})
        state=read_json(self.root/'state.json');files,dirs=scan(self.root/'Files')
        return {'projectId':pid,'workspacePath':str(self.root/'Files'),'status':state.get('status'),'localFiles':len(files),'localBytes':sum(regular(p).st_size for p in files.values()),'baselineFiles':len(state.get('files',[])),'savedHead':state.get('savedHead'),'catalogId':marker.get('catalogId'),'contentVerification':'Not performed by status; use verify or save.'}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default=r'\\MSI\ProjectLibrary');parser.add_argument('--returns',default=r'\\MSI\ProjectReturns')
    parser.add_argument('--cache',default=str(Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'ProjectLibrary/Cache'));parser.add_argument('--test-local',action='store_true')
    sub=parser.add_subparsers(dest='command',required=True);sub.add_parser('list')
    for command in ('fetch','status','save','release','verify'):
        p=sub.add_parser(command);p.add_argument('project_id')
        if command=='release':p.add_argument('--confirm',required=True)
    args=parser.parse_args(argv)
    try:
        client=Client(args)
        result=client.list() if args.command=='list' else client.release(args.project_id,args.confirm) if args.command=='release' else getattr(client,args.command)(args.project_id)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except KeyboardInterrupt:
        print(json.dumps({'status':'INTERRUPTED','error':'Operation interrupted. Sources and committed versions remain intact; owned partial work is retained. Inspect status before resuming.'}));return 130
    except (LibraryError,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':sys.exit(main())
