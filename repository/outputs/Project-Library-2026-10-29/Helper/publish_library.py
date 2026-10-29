"""Publish a curated local Windows library without copying ordinary payloads.

Ordinary files are mutable NTFS hardlinks. Never change their ACLs/attributes.
Only the known active Stats database is copied, using SQLite's online backup API.
The client must hold .library-access.lock open for read with FILE_SHARE_READ for
the whole operation. This publisher holds the same file with share mode zero.
No SMB configuration, network connection, source modification, or pruning occurs.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
import time
import uuid

FORMAT='project-library-catalog-v1'
CATALOG_ID='msi-project-library-v1'
ACTIVE_DB=r'C:\Users\jlewi\AppData\Local\TheStats\stats.sqlite3'
BAD_ATTRIBUTES=0x400|0x1000|0x40000|0x400000
RESERVE=2*1024**3
MAX_SNAPSHOT=128*1024**2
LOCK_NAME='.library-access.lock'
STAMP=lambda:datetime.now(timezone.utc).isoformat()

class UnsafePublication(RuntimeError):pass
class SourceUnavailable(OSError):pass
def need(condition,message):
    if not condition:raise UnsafePublication(message)
def canonical(path):return ntpath.normcase(ntpath.normpath(str(path)))
def fs(path):
    value=str(Path(path).absolute())
    return value if value.startswith('\\\\?\\') else '\\\\?\\'+value
def relative(value):
    need(isinstance(value,str) and value and '\\' not in value and ':' not in value,'Invalid relative path')
    parts=value.split('/')
    need(all(p not in ('','.','..') and not p.endswith((' ','.')) and not re.match(r'^(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)',p,re.I) for p in parts),'Unsafe relative path: '+value)
    need(not value.startswith('/') and all(not any(c in p for c in '<>"|?*') and not any(ord(c)<32 for c in p) for p in parts),'Unsafe path characters')
    return value
def absolute(value):
    p=Path(value).absolute()
    need(p.drive.upper()=='C:' and not str(p).startswith('\\\\'),'Library sources/output must be local C: paths')
    need('..' not in p.parts,'Parent traversal is not allowed')
    return p
def info(path):return os.lstat(fs(path))
def exists(path):
    try:info(path);return True
    except FileNotFoundError:return False
def clean_path(path,require_file=False):
    p=absolute(path)
    for item in [p,*p.parents]:
        try:s=info(item)
        except FileNotFoundError:continue
        need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&BAD_ATTRIBUTES,'Link, offline or recall path refused: '+str(item))
        if item==p and require_file:need(stat.S_ISREG(s.st_mode),'Expected regular file: '+str(p))
        elif item!=p:need(stat.S_ISDIR(s.st_mode),'Non-directory ancestor: '+str(item))
    if require_file:need(exists(p),'Source file missing: '+str(p))
    return p
def mkdir(path):
    clean_path(path);os.makedirs(fs(path),exist_ok=True);clean_path(path)
    need(stat.S_ISDIR(info(path).st_mode),'Output path is not a directory')
def load_json(path):
    clean_path(path,True)
    with open(fs(path),encoding='utf-8-sig') as f:return json.load(f)
def digest(path):
    with open(fs(path),'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def atomic_json(path,value):
    clean_path(path);mkdir(path.parent)
    temp=path.with_name(path.name+'.new-'+uuid.uuid4().hex)
    with open(fs(temp),'x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
    if exists(path):
        clean_path(path,True);need(info(path).st_nlink==1,'Refusing to replace multiply-linked publication metadata')
    os.replace(fs(temp),fs(path))

kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
kernel.CreateFileW.restype=wintypes.HANDLE
kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
class FileInfo(ctypes.Structure):
    _fields_=[('attributes',wintypes.DWORD),('created',wintypes.FILETIME),('accessed',wintypes.FILETIME),('written',wintypes.FILETIME),('volume',wintypes.DWORD),('sizeHigh',wintypes.DWORD),('sizeLow',wintypes.DWORD),('links',wintypes.DWORD),('idHigh',wintypes.DWORD),('idLow',wintypes.DWORD)]
kernel.GetFileInformationByHandle.argtypes=[wintypes.HANDLE,ctypes.POINTER(FileInfo)];kernel.GetFileInformationByHandle.restype=wintypes.BOOL
def handle_info(handle):
    data=FileInfo()
    if not kernel.GetFileInformationByHandle(handle,ctypes.byref(data)):raise ctypes.WinError(ctypes.get_last_error())
    need(not data.attributes&BAD_ATTRIBUTES and not data.attributes&0x10,'Unsafe source handle')
    ticks=(data.written.dwHighDateTime<<32)|data.written.dwLowDateTime
    return dict(volume=data.volume,fileId=(data.idHigh<<32)|data.idLow,bytes=(data.sizeHigh<<32)|data.sizeLow,mtime_ns=(ticks-116444736000000000)*100)
@contextlib.contextmanager
def source_handle(path,allow_unavailable=False,allow_delete=True):
    try:clean_path(path,True)
    except (UnsafePublication,OSError) as exc:
        if allow_unavailable:raise SourceUnavailable(str(exc)) from exc
        raise
    # Attributes-only access, no recall. Share delete is required for renaming our
    # hardlink. Verify the new link against this handle's identity before publishing.
    # A later replacement of the original pathname is detected on the next refresh.
    h=kernel.CreateFileW(fs(path),0x80,7 if allow_delete else 3,None,3,0x00200000|0x00100000,None)
    if h==ctypes.c_void_p(-1).value:
        exc=ctypes.WinError(ctypes.get_last_error())
        if allow_unavailable:raise SourceUnavailable(str(exc)) from exc
        raise exc
    try:
        try:record=handle_info(h)
        except UnsafePublication as exc:
            if allow_unavailable:raise SourceUnavailable(str(exc)) from exc
            raise
        yield h,record
    finally:kernel.CloseHandle(h)
@contextlib.contextmanager
def publish_lock(root):
    path=root/LOCK_NAME;clean_path(path)
    if exists(path):need(info(path).st_nlink==1,'Lock file must not be a hardlink')
    h=kernel.CreateFileW(fs(path),0xC0000000,0,None,4,0x00200000|0x00100000,None)
    if h==ctypes.c_void_p(-1).value:raise RuntimeError('Library is in use or publisher lock is unavailable: '+str(ctypes.WinError(ctypes.get_last_error())))
    try:
        handle_info(h)
        yield
    finally:kernel.CloseHandle(h)
def identity(record):return record['volume'],record['fileId']
def file_identity(path):
    with source_handle(path) as (_,record):return record

def validate_catalog(path):
    catalog=load_json(path)
    need(catalog.get('format')==FORMAT and catalog.get('catalogId')==CATALOG_ID,'Unexpected catalog format or identity')
    need(catalog.get('publicationStatus')=='SOURCE_CATALOG_PREPARED','Expected prepared source catalog')
    sources={};destinations=set();directories=set()
    for row in catalog.get('publishingSources',[]):
        rel=relative(row['path']);source=absolute(row['source']);key=rel.casefold()
        need(rel=='Drive_C/'+'/'.join(source.parts[1:]),'Source/destination mapping is not original Drive_C layout')
        need(key not in destinations and key not in directories,'Destination collision: '+rel)
        for parent in PurePosixPath(rel).parents:
            if str(parent)=='.':continue
            need(str(parent).casefold() not in destinations,'File/directory collision');directories.add(str(parent).casefold())
        destinations.add(key)
        need(canonical(source) not in sources,'Duplicate absolute source')
        need(row.get('kind') in ('file','sqlite','jsonl'),'Invalid source kind')
        need(type(row.get('bytes')) is int and row['bytes']>=0 and type(row.get('mtime_ns')) is int,'Invalid file metadata')
        if row['kind']=='sqlite':need(canonical(source)==canonical(ACTIVE_DB),'Only the known active Stats database may use snapshot publishing')
        need(canonical(source) not in {canonical(ACTIVE_DB+s) for s in ('-wal','-shm','-journal')},'Active SQLite sidecar is forbidden')
        sources[canonical(source)]=row
    by_path={r['path']:r for r in sources.values()};projects=set();referenced=set()
    for project in catalog['projects']:
        gid=project['id'];need(re.fullmatch('[a-z0-9-]+',gid) and gid not in projects,'Invalid or duplicate project id');projects.add(gid);seen=set()
        for row in project['files']:
            rel=relative(row['path']);need(rel in by_path,'Project references unknown source');need(rel.casefold() not in seen,'Duplicate file within project');seen.add(rel.casefold());referenced.add(rel)
            need(row['sourceRelative']=='Sources/'+rel,'Incorrect source-relative path')
            expected='sqlite_snapshot' if by_path[rel]['kind']=='sqlite' else by_path[rel]['kind']
            need(row['kind']==expected,'Project/source kind mismatch')
        for value in project.get('workspacePaths',[]):need(relative(value)=='Files' or relative(value).startswith('Files/'),'Workspace path must stay inside Files')
    need(referenced==set(by_path),'Registry contains sources absent from projects')
    return catalog,by_path

def load_ownership(root):
    records={};directory=root/'.publish-journals'
    if exists(directory):clean_path(directory)
    for path in sorted(directory.glob('*.jsonl')) if exists(directory) else []:
        clean_path(path,True)
        with open(fs(path),'rb') as f:
            for line in f:
                if not line.endswith(b'\n'):break # interrupted final record; leave original journal intact
                record=json.loads(line)
                if record.get('status')=='PUBLISHED':records[relative(record['path'])]=record
    intents=root/'.snapshot-intents'
    if exists(intents):
        clean_path(intents)
        for path in sorted(intents.glob('*.json')):
            record=load_json(path);rel=relative(record['path']);target=root/'Sources'/rel
            if not exists(target):continue
            if identity(file_identity(target))!=identity(record['identity']):continue
            previous=records.get(rel)
            if not previous or record['publishedUtc']>previous.get('publishedUtc',''):records[rel]=record
    return records
def publish_target(temp,target,root,run_id,old_record,refresh):
    """Replace only a verified owned published link; retain old link/snapshot bytes."""
    if exists(target):
        clean_path(target,True)
        old_identity=file_identity(target)
        need(old_record and identity(old_record['identity'])==identity(old_identity),'Existing publication is unowned or replaced: '+str(target))
        need(refresh,'Source was replaced or a new snapshot is ready; use --refresh to preserve and retire the old publication')
        retired=root/'.retired'/run_id/target.relative_to(root)
        mkdir(retired.parent);need(not exists(retired),'Retired path already exists')
        os.link(fs(target),fs(retired),follow_symlinks=False)
        need(identity(file_identity(retired))==identity(old_identity),'Retired link identity mismatch')
    clean_path(target.parent)
    os.replace(fs(temp),fs(target))

def snapshot_database(source,temp):
    started=time.monotonic();mkdir(temp.parent);need(not exists(temp),'Snapshot temporary path already exists')
    def progress(status,remaining,total):
        if time.monotonic()-started>120:raise TimeoutError('SQLite backup exceeded 120 seconds')
        if shutil.disk_usage(temp.parent).free<RESERVE:raise RuntimeError('Disk reserve reached')
        if exists(temp) and info(temp).st_size>MAX_SNAPSHOT:raise RuntimeError('SQLite snapshot exceeds 128 MiB budget')
    try:
        with contextlib.closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10)) as src:
            page_size=src.execute('PRAGMA page_size').fetchone()[0];page_count=src.execute('PRAGMA page_count').fetchone()[0]
            need(page_size*page_count<=MAX_SNAPSHOT,'Active database exceeds the 128 MiB snapshot budget')
            with contextlib.closing(sqlite3.connect(str(temp))) as dst:
                src.backup(dst,pages=512,progress=progress,sleep=0.05)
                need([r[0] for r in dst.execute('PRAGMA integrity_check')]==['ok'],'SQLite snapshot integrity check failed')
        need(info(temp).st_size<=MAX_SNAPSHOT,'SQLite snapshot size limit exceeded')
        return dict(sha256=digest(temp),integrity_check='ok',createdUtc=STAMP())
    except BaseException:
        # Only this invocation's randomly named workspace output is eligible for removal.
        for p in [temp,Path(str(temp)+'-journal'),Path(str(temp)+'-wal'),Path(str(temp)+'-shm')]:
            if exists(p):clean_path(p,True);os.unlink(fs(p))
        raise

def publish_one(row,root,run_id,owned,refresh):
    source=absolute(row['source']);target=root/'Sources'/relative(row['path']);mkdir(target.parent)
    need(root not in source.parents and source!=root,'Source cannot be inside publication output')
    old=owned.get(row['path'])
    if old:need(canonical(old['source'])==canonical(source),'Published path was previously assigned to another source')
    with source_handle(source,allow_unavailable=True,allow_delete=row['kind']!='sqlite') as (_,source_id):
        changed=(row['bytes'],row['mtime_ns'])!=(source_id['bytes'],source_id['mtime_ns'])
        if row['kind']=='sqlite':
            if exists(target) and not refresh:
                need(old and old.get('kind')=='sqlite_snapshot','Existing database publication lacks ownership evidence')
                now=file_identity(target);need(identity(now)==identity(old['identity']),'Published snapshot identity changed')
                need(digest(target)==old['snapshot']['sha256'],'Published snapshot bytes changed')
                return dict(old,sourceMetadataChanged=changed,action='REUSED_EXISTING_STATIC_SNAPSHOT')
            temp=root/'.staging'/('stats-'+uuid.uuid4().hex+'.sqlite3');snapshot=snapshot_database(source,temp)
            intent=dict(status='PUBLISHED',path=row['path'],source=str(source),kind='sqlite_snapshot',identity=file_identity(temp),sourceIdentity=source_id,snapshot=snapshot,sourceMetadataChanged=changed,action='SQLITE_ONLINE_BACKUP',publishedUtc=STAMP())
            # Persist identity/hash before publishing the pathname. A crash between
            # os.replace and journal commit can safely recover this exact snapshot.
            atomic_json(root/'.snapshot-intents'/(run_id+'-'+uuid.uuid4().hex+'.json'),intent)
            publish_target(temp,target,root,run_id,old,refresh)
            dest_id=file_identity(target)
            need(identity(dest_id)==identity(intent['identity']),'Published snapshot identity changed')
            return dict(intent,identity=dest_id)
        if exists(target):
            now=file_identity(target)
            if identity(now)==identity(source_id):
                return dict(status='PUBLISHED',path=row['path'],source=str(source),kind=row['kind'],identity=now,sourceMetadataChanged=changed,action='EXISTING_SAME_SOURCE_HARDLINK')
            need(old and identity(now)==identity(old['identity']),'Existing destination is not the recorded published file')
            need(refresh,'Source path now names a different file; explicit --refresh is required')
        temp=target.with_name('.link-'+uuid.uuid4().hex)
        need(not exists(temp),'Unexpected temporary hardlink')
        try:
            os.link(fs(source),fs(temp),follow_symlinks=False)
            need(identity(file_identity(temp))==identity(source_id),'New hardlink does not name the source file')
            publish_target(temp,target,root,run_id,old,refresh)
        finally:
            if exists(temp):clean_path(temp,True);os.unlink(fs(temp))
        return dict(status='PUBLISHED',path=row['path'],source=str(source),kind=row['kind'],identity=file_identity(target),sourceMetadataChanged=changed,action='HARDLINK_CREATED')

def run(args):
    source_catalog=absolute(args.source_catalog);catalog,by_path=validate_catalog(source_catalog);root=absolute(args.output)
    need(root!=Path('C:/') and len(root.parts)>3,'Output must be a dedicated subdirectory')
    for row in by_path.values():need(root not in absolute(row['source']).parents,'Output contains a selected source')
    preview=dict(status='DRY_RUN' if args.dry_run else 'PUBLICATION_PLAN',output=str(root),projects=len(catalog['projects']),uniqueSources=len(by_path),hardlinks=sum(r['kind']!='sqlite' for r in by_path.values()),sqliteSnapshots=sum(r['kind']=='sqlite' for r in by_path.values()),logicalBytes=sum(r['bytes'] for r in by_path.values()),ordinaryPayloadBytesCopied=0,estimatedSnapshotBytes=sum(r['bytes'] for r in by_path.values() if r['kind']=='sqlite'),catalogSourceBytes=info(source_catalog).st_size,deferred=len(catalog.get('deferred',[])),filesystemMetadataOverheadNotIncluded=True,noNetworkConfiguration=True)
    print(json.dumps(preview,ensure_ascii=False),flush=True)
    if args.dry_run:return preview
    if exists(root):
        clean_path(root);owner=root/'.library-owner.json'
        if exists(owner):
            o=load_json(owner);need(o.get('format')=='project-library-owner-v1' and o.get('catalogId')==CATALOG_ID and canonical(o.get('root',''))==canonical(root),'Output ownership marker mismatch')
        else:need(not any(root.iterdir()),'Refusing to modify a nonempty unowned output directory')
    mkdir(root)
    with publish_lock(root):
        owner=root/'.library-owner.json'
        if exists(owner):
            o=load_json(owner);need(o.get('format')=='project-library-owner-v1' and o.get('catalogId')==CATALOG_ID and canonical(o.get('root',''))==canonical(root),'Output ownership marker mismatch')
        else:
            need(set(p.name for p in root.iterdir())=={LOCK_NAME},'Refusing to adopt a nonempty output directory')
            atomic_json(owner,dict(format='project-library-owner-v1',catalogId=CATALOG_ID,root=str(root),createdUtc=STAMP()))
        need(shutil.disk_usage(root).free>=RESERVE+MAX_SNAPSHOT+info(source_catalog).st_size*2,'Insufficient publication headroom')
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:10]
        owned=load_ownership(root);journal_dir=root/'.publish-journals';mkdir(journal_dir)
        available={};failures=[];actions={};changed=0
        with open(fs(journal_dir/(run_id+'.jsonl')),'x',encoding='utf-8') as journal:
            for index,row in enumerate(by_path.values(),1):
                try:
                    record=publish_one(row,root,run_id,owned,args.refresh)
                    record['publishedUtc']=STAMP();journal.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
                    if index%100==0:journal.flush();os.fsync(journal.fileno())
                    available[row['path']]=record;actions[record['action']]=actions.get(record['action'],0)+1;changed+=bool(record.get('sourceMetadataChanged'))
                except UnsafePublication:raise # ownership/path/identity failures are fail-closed
                except (OSError,sqlite3.Error,TimeoutError) as exc:
                    failures.append(dict(source=row['source'],path=row['path'],reason=str(exc),status='CURRENT_SOURCE_UNAVAILABLE'))
                if index%1000==0:
                    need(shutil.disk_usage(root).free>=RESERVE,'Disk reserve reached')
                    print(json.dumps(dict(processed=index,total=len(by_path),published=len(available),unavailable=len(failures))),flush=True)
            journal.flush();os.fsync(journal.fileno())
        published={k:v for k,v in catalog.items() if k!='publishingSources'}
        for project in published['projects']:
            project['unavailableFiles']=[dict(path=r['path'],reason='Current source unavailable; see publication report') for r in project['files'] if r['path'] not in available]
            new=[]
            for row in project['files']:
                if row['path'] not in available:continue
                r=available[row['path']];fresh=dict(row,bytes=r['identity']['bytes'],mtime_ns=r['identity']['mtime_ns'])
                if r['kind']=='sqlite_snapshot':fresh['snapshotSha256']=r['snapshot']['sha256']
                new.append(fresh)
            project['files']=new;project['summary']=dict(files=len(new),bytes=sum(r['bytes'] for r in new),unavailableFiles=len(project['unavailableFiles']))
        published.update(publicationStatus='PUBLISHED_WITH_OMISSIONS' if failures or published.get('deferred') or published.get('exclusions') else 'PUBLISHED',publishedUtc=STAMP(),publicationId=run_id,sourceCatalogSha256=digest(source_catalog),lockProtocol=dict(path=LOCK_NAME,clientAccess='GENERIC_READ',clientShareMode='FILE_SHARE_READ',publisherShareMode=0))
        published['summary'].update(publishedUniqueFiles=len(available),publishedLogicalBytes=sum(r['identity']['bytes'] for r in available.values()),currentUnavailableFiles=len(failures))
        published.setdefault('deferred',[]).extend(failures)
        report=dict(format='project-library-publication-report-v1',publicationId=run_id,createdUtc=STAMP(),sourceCatalog=str(source_catalog),sourceCatalogSha256=published['sourceCatalogSha256'],summary=published['summary'],actions=actions,sourceMetadataChanges=changed,currentUnavailable=failures,originalsUnmodified=True,hardlinksAreMutable=True,retiredPublicationsPreserved=True,noSMBConfigured=True)
        atomic_json(root/'publication-report.json',report)
        atomic_json(root/'catalog.json',published)
        print(json.dumps(dict(status=published['publicationStatus'],catalog=str(root/'catalog.json'),report=str(root/'publication-report.json'),summary=published['summary'],actions=actions),ensure_ascii=False),flush=True)
        return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-catalog',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--dry-run',action='store_true',help='Validate stored catalog and print counts; no publication and no per-source metadata scan')
    parser.add_argument('--refresh',action='store_true',help='Refresh replaced sources/database snapshot under the library lock; retain old publications in .retired')
    args=parser.parse_args()
    try:run(args)
    except Exception as exc:print(json.dumps(dict(status='PUBLICATION_STOPPED',error=str(exc))),file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
