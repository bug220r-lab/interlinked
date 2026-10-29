"""Read-only project capture into checkpointed, SHA-256 deduplicated ZIP batches.

Sources are never modified. This module's ctypes calls are filesystem handles,
not UI automation. Output and state are confined to explicitly supplied paths.
"""
import argparse
import contextlib
import ctypes
import hashlib
import io
import json
import msvcrt
import os
import re
import shutil
import sqlite3
import stat
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from restore_export import safe_relative as restore_safe_relative

CHUNK = 4 * 1024 * 1024
RESERVE = 2 * 1024**3
SKIP_DIRS = {s.casefold() for s in ['.git','.venv','venv','node_modules','__pycache__','.pytest_cache','.cache','Cache','Caches','tmp','temp','.ssh','.aws','.azure','.kube','.gnupg']}
SECRET = re.compile(r'recovery[ _-]*codes?|backup[ _-]*codes?|password|credential|secret|(^|[._ -])tokens?([._ -]|$)|private[ _-]*key|^auth\.(json|toml|yaml|yml)$|^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|pfx|p12|key)$',re.I)
ACTIVE_DB = Path(r'C:\Users\jlewi\AppData\Local\TheStats\stats.sqlite3')
STAMP = lambda: datetime.now(timezone.utc).isoformat()

class InventoryPathError(RuntimeError):
    pass

class ArchiveFatalError(RuntimeError):
    pass

def dump(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.new')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)

def digest(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def under(child,parent):
    c=os.path.normcase(os.path.abspath(child));p=os.path.normcase(os.path.abspath(parent))
    return c==p or c.startswith(p.rstrip('\\')+'\\')

def safe_relative(path):
    try:return restore_safe_relative(path)
    except Exception as exc:raise InventoryPathError(str(exc)) from exc

def validate_destinations(rows):
    files=set();directories=set()
    for row in rows:
        key=safe_relative(row['path']).casefold()
        if key in files or key in directories:raise InventoryPathError('Duplicate or conflicting destination: '+row['path'])
        pieces=key.split('/')
        for i in range(1,len(pieces)):
            parent='/'.join(pieces[:i])
            if parent in files:raise InventoryPathError('File/directory destination conflict: '+row['path'])
            directories.add(parent)
        files.add(key)

def file_reason(path,st):
    attrs=getattr(st,'st_file_attributes',0)
    if attrs & (0x400|0x1000|0x40000|0x400000): return 'Link/reparse/offline/recall path; not read'
    if stat.S_ISLNK(st.st_mode): return 'Symbolic link; not read'
    return None

def ensure_ancestors(path):
    for p in [Path(path),*Path(path).parents]:
        if p.exists():
            reason=file_reason(p,p.lstat())
            if reason: raise RuntimeError(str(p)+': '+reason)

def enumerate_sources(plan,output):
    excluded=[];files=[];seen={}
    roots=[e for e in plan['entries'] if e.get('enabled') is True]
    explicit=[Path(p) for p in plan.get('excludePaths',[])]
    for e in roots:
        source=Path(e['source'])
        if under(output,source) and not any(under(output,x) for x in explicit):
            raise RuntimeError('Output is inside an unexcluded source')
        base=safe_relative(e['relativeDestination'])
        stack=[(source,base)]
        while stack:
            p,rel=stack.pop()
            try:
                st=p.lstat()
                reason=file_reason(p,st)
                if not reason and any(under(p,x) for x in explicit): reason='Explicit transfer-plan exclusion; subtree not inventoried'
                if not reason and p.is_dir() and p.name.casefold() in SKIP_DIRS: reason='Regenerable/sensitive/Git directory excluded'
                if reason:
                    excluded.append(dict(source=str(p),path=rel,reason=reason));continue
                if p.is_dir():
                    children=list(p.iterdir())
                    if not children: excluded.append(dict(source=str(p),path=rel,reason='Empty directory; not recreated'))
                    for child in children: stack.append((child,rel+'/'+child.name))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    excluded.append(dict(source=str(p),path=rel,reason='Non-regular file'));continue
                name=p.name
                if SECRET.search(name) or (re.search(r'^\.env($|\.)',name,re.I) and not re.search(r'\.(example|sample|template)$',name,re.I)):
                    excluded.append(dict(source=str(p),path=rel,reason='Credential-like filename; no content read'));continue
                # The active library's journal state belongs in its consistent DB snapshot.
                if str(p) in [str(ACTIVE_DB)+s for s in ['-wal','-shm','-journal']]:
                    excluded.append(dict(source=str(p),path=rel,reason='Active SQLite sidecar; committed content captured in database snapshot'));continue
                rel=safe_relative(rel)
                if rel.casefold() in seen: raise InventoryPathError('Duplicate destination: '+rel)
                seen[rel.casefold()]=True
                files.append(dict(source=str(p),path=rel,bytes=st.st_size,mtime_ns=st.st_mtime_ns))
            except InventoryPathError:raise
            except Exception as exc:
                excluded.append(dict(source=str(p),path=rel,reason='INVENTORY_ERROR: '+str(exc)))
    validate_destinations(files)
    return files,excluded

kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
kernel.CreateFileW.restype=ctypes.c_void_p
kernel.CloseHandle.argtypes=[ctypes.c_void_p]
kernel.CloseHandle.restype=ctypes.c_int

@contextlib.contextmanager
def locked_reader(path,allow_writers=False):
    ensure_ancestors(path)
    full=str(Path(path).absolute())
    winpath=chr(92)*2+'?'+chr(92)+full if not full.startswith(chr(92)*2) else full
    # FILE_FLAG_SEQUENTIAL_SCAN | OPEN_REPARSE_POINT | OPEN_NO_RECALL.
    handle=kernel.CreateFileW(winpath,0x80000000,7 if allow_writers else 1,None,3,0x08200000|0x00100000,None)
    if handle==ctypes.c_void_p(-1).value:
        error=ctypes.get_last_error();raise OSError(error,ctypes.FormatError(error),full)
    try: fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle);raise
    with os.fdopen(fd,'rb',buffering=CHUNK) as stream:
        reason=file_reason(path,os.fstat(stream.fileno()))
        if reason: raise RuntimeError(reason)
        yield stream

def last_newline(stream,length):
    pos=length
    while pos:
        begin=max(0,pos-CHUNK);stream.seek(begin);raw=stream.read(pos-begin)
        found=raw.rfind(b'\n')
        if found>=0:return begin+found+1
        pos=begin
    return 0

def hash_length(stream,length):
    h=hashlib.sha256();stream.seek(0);left=length
    while left:
        b=stream.read(min(CHUNK,left))
        if not b:raise RuntimeError('Source shortened during capture')
        h.update(b);left-=len(b)
    return h.hexdigest()

@contextlib.contextmanager
def capture_reader(row,scratch):
    source=Path(row['source']);method='byte_exact_read_lock';details={}
    is_active=os.path.normcase(str(source))==os.path.normcase(str(ACTIVE_DB))
    if is_active and any(Path(str(source)+s).exists() for s in ('-wal','-journal')):
        temp=Path(scratch)/('active-stats-'+str(os.getpid())+'.sqlite3')
        if temp.exists(): raise RuntimeError('Unexpected existing snapshot temporary file')
        started=time.monotonic()
        def progress(status,remaining,total):
            if time.monotonic()-started>120:raise TimeoutError('SQLite snapshot timed out')
            if shutil.disk_usage(temp.parent).free<RESERVE:raise RuntimeError('Disk reserve reached during SQLite snapshot')
        try:
            with contextlib.closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10)) as src:
                with contextlib.closing(sqlite3.connect(temp)) as dst:
                    src.backup(dst,pages=1024,progress=progress,sleep=0.05)
                    result=[r[0] for r in dst.execute('PRAGMA integrity_check')]
                    if result!=['ok']:raise RuntimeError('Active DB snapshot failed integrity_check')
            with locked_reader(temp) as stream:
                yield stream,os.fstat(stream.fileno()).st_size,'sqlite_online_backup',dict(integrity_check='ok',source_mtime_ns=source.stat().st_mtime_ns)
        finally:
            if temp.exists():temp.unlink() # Only this function's workspace temporary snapshot.
        return
    is_log=source.suffix.casefold()=='.jsonl' and '\\sessions\\' in str(source).casefold()
    if is_log:
        with locked_reader(source,allow_writers=True) as stream:
            start=os.fstat(stream.fileno());length=last_newline(stream,start.st_size)
            if start.st_size and not length:raise RuntimeError('No complete JSONL record at snapshot cutoff')
            yield stream,length,'complete_line_prefix_snapshot',dict(source_bytes_at_start=start.st_size,prefix_cutoff_bytes=length)
        return
    with locked_reader(source) as stream:
        st=os.fstat(stream.fileno())
        if is_active:
            if any(Path(str(source)+s).exists() for s in ('-wal','-journal')):raise RuntimeError('Active DB journal appeared; retry using backup API')
            with contextlib.closing(sqlite3.connect(source.as_uri()+'?mode=ro&immutable=1',uri=True)) as check:
                result=[r[0] for r in check.execute('PRAGMA integrity_check')]
            if result!=['ok']:raise RuntimeError('Locked active DB failed integrity_check')
            details['integrity_check']='ok'
        yield stream,st.st_size,method,details

def verify_part(path,blobs):
    with zipfile.ZipFile(path) as z:
        if len(z.infolist())!=len(blobs):raise RuntimeError('ZIP member count mismatch')
        for sha,item in blobs.items():
            with z.open(item['member']) as f:
                h=hashlib.sha256();length=0
                while b:=f.read(CHUNK):h.update(b);length+=len(b)
            if h.hexdigest()!=sha or length!=item['bytes']:raise RuntimeError('Archived blob verification failed')
    return digest(path)

def run(args):
    state_dir=Path(args.state).absolute();output=Path(args.output).absolute();state_dir.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    ensure_ancestors(state_dir);ensure_ancestors(output)
    if (output/'catalog.json').exists():raise RuntimeError('Export is already complete; catalog.json exists')
    state_file=state_dir/'checkpoint.json'
    if state_file.exists():state=json.loads(state_file.read_text(encoding='utf-8'))
    else:
        plan=json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
        files,exclusions=enumerate_sources(plan,output)
        # Retain the earlier sealed recovery kit, including its captured Git histories.
        supporting=Path(args.supporting_kit) if args.supporting_kit else None
        if supporting:
            st=supporting.stat();files.append(dict(source=str(supporting),path='Supporting/'+supporting.name,bytes=st.st_size,mtime_ns=st.st_mtime_ns))
        validate_destinations(files)
        def priority(r):
            p=Path(r['source']);name=p.name.casefold()
            if p==ACTIVE_DB:return (0,-r['bytes'],r['path'])
            if name.endswith('.gguf') or '.gguf.' in name:return (4,-r['bytes'],r['path'])
            if r['bytes']>32*1024**2 and (p.suffix.casefold() in ('.zip','.zst','.7z','.gz','.mp4','.bin') or re.search(r'\.part\d+$',name)):return (3,-r['bytes'],r['path'])
            return (1,-r['bytes'],r['path'])
        files.sort(key=priority)
        state=dict(format='codex-capture-checkpoint-v1',output=str(output),createdUtc=STAMP(),next_index=0,next_part=1,next_batch=1,inventory=files,work_queue=list(range(len(files))),exclusions=exclusions,records=[],blobs={},parts=[],errors=[],error_history=[],batches=[])
        dump(state_file,state)
        dump(state_dir/'inventory-summary.json',dict(files=len(files),logicalBytes=sum(r['bytes'] for r in files),exclusions=len(exclusions),errors=sum(e['reason'].startswith('INVENTORY_ERROR') for e in exclusions)))
    if os.path.normcase(state['output'])!=os.path.normcase(str(output)):raise RuntimeError('Output differs from saved capture state')
    validate_destinations(state['inventory']);validate_destinations(state['records'])
    state.setdefault('work_queue',list(range(len(state['inventory']))))
    state.setdefault('error_history',[])
    recorded_paths={r['path'].casefold() for r in state['records']}
    if state['next_index']>=len(state['work_queue']) and state['errors']:
        by_path={r['path'].casefold():i for i,r in enumerate(state['inventory'])}
        retries=[]
        for error in state['errors']:
            index=error.get('inventory_index',by_path.get(error['path'].casefold()))
            if index is None:raise RuntimeError('Cannot resolve failed source to immutable inventory')
            if state['inventory'][index]['path'].casefold() not in recorded_paths and index not in retries:retries.append(index)
            state['error_history'].append(dict(error,retryScheduledUtc=STAMP()))
        state['work_queue'].extend(retries);state['errors']=[]
        dump(state_file,state)
    batch_no=state['next_batch'];batch=output/f'Batch-{batch_no:03d}'
    if batch.exists():raise RuntimeError('Batch directory already exists; inspect interrupted work before resuming')
    batch.mkdir()
    zip_obj=None;part_path=None;part_name=None;part_blobs={};batch_blobs={};batch_parts=[];batch_bytes=0;start_records=len(state['records']);started=STAMP();start_time=time.monotonic()
    stopped='BATCH_LIMIT';last_progress=0

    def progress(force=False):
        nonlocal last_progress
        now=time.monotonic()
        if force or now-last_progress>5:
            info=dict(batch=batch_no,filesProcessed=state['next_index'],workQueueEntries=len(state['work_queue']),totalFiles=len(state['inventory']),capturedFiles=len(state['records']),logicalCapturedBytes=sum(r['bytes'] for r in state['records']),uniqueBlobs=len(state['blobs']),batchFinalizedBytes=batch_bytes,currentPartBytes=part_path.stat().st_size if part_path and part_path.exists() else 0,errors=len(state['errors']),freeBytes=shutil.disk_usage(output).free,elapsedSeconds=round(now-start_time),updatedUtc=STAMP())
            dump(state_dir/'progress.json',info);last_progress=now

    def finish_part():
        nonlocal zip_obj,part_path,part_name,part_blobs,batch_bytes
        if zip_obj is None:return
        try:
            zip_obj.close();zip_obj=None
            sha=verify_part(part_path,part_blobs)
            final=part_path.with_suffix('') # Remove .partial, preserving .zip.
            if final.exists():raise RuntimeError('Refusing to replace archive part')
            part_path.rename(final)
            rec=dict(name=part_name,bytes=final.stat().st_size,sha256=sha)
            state['parts'].append(rec);batch_parts.append(rec);batch_bytes+=rec['bytes'];batch_blobs.update(part_blobs)
            part_path=None;part_name=None;part_blobs={}
            dump(state_file,state);progress(True)
        except Exception as exc:raise ArchiveFatalError('Archive finalization failed: '+repr(exc)) from exc

    try:
        while state['next_index']<len(state['work_queue']):
            inventory_index=state['work_queue'][state['next_index']]
            row=state['inventory'][inventory_index]
            if row['path'].casefold() in recorded_paths:
                state['next_index']+=1;continue
            if batch_bytes>=args.batch_bytes:
                stopped='BATCH_LIMIT';break
            try:
                with capture_reader(row,state_dir) as (src,length,method,details):
                    before=os.fstat(src.fileno());sha=hash_length(src,length)
                    if sha not in state['blobs']:
                        current=part_path.stat().st_size if part_path and part_path.exists() else 0
                        if current and current+length>args.part_bytes:finish_part()
                        if batch_bytes and batch_bytes+length>args.batch_bytes:
                            stopped='BATCH_LIMIT';break
                        if shutil.disk_usage(output).free<length+RESERVE+64*1024**2:
                            stopped='DISK_RESERVE';break
                        try:
                            if zip_obj is None:
                                part_name=f'project-part-{state["next_part"]:04d}.zip';state['next_part']+=1
                                part_path=batch/(part_name+'.partial')
                                zip_obj=zipfile.ZipFile(part_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True)
                            member='blobs/'+sha;src.seek(0);second=hashlib.sha256();left=length
                            with zip_obj.open(member,'w',force_zip64=True) as dest:
                                while left:
                                    block=src.read(min(CHUNK,left))
                                    if not block:raise RuntimeError('Source shortened during archive write')
                                    if shutil.disk_usage(output).free<RESERVE:raise RuntimeError('DISK_FLOOR_DURING_WRITE')
                                    dest.write(block);second.update(block);left-=len(block);progress()
                            if second.hexdigest()!=sha:raise RuntimeError('SOURCE_CHANGED_DURING_ARCHIVE_WRITE')
                            after=os.fstat(src.fileno())
                            if method=='byte_exact_read_lock' and (after.st_size!=before.st_size or after.st_mtime_ns!=before.st_mtime_ns):raise RuntimeError('Locked source metadata changed')
                        except Exception as exc:raise ArchiveFatalError('Archive write failed: '+repr(exc)) from exc
                        blob=dict(part=part_name,member=member,bytes=length)
                        state['blobs'][sha]=blob;part_blobs[sha]=blob
                    else:
                        # Verify the fixed prefix again even when its hash already exists.
                        if method=='complete_line_prefix_snapshot' and hash_length(src,length)!=sha:raise RuntimeError('Active log prefix changed during capture')
                    record=dict(path=row['path'],sha256=sha,bytes=length,mtime_ns=before.st_mtime_ns,source=row['source'],capture_method=method,capturedUtc=STAMP(),inventory_bytes=row['bytes'],inventory_mtime_ns=row['mtime_ns'],**details)
            except ArchiveFatalError:raise
            except Exception as exc:
                state['errors'].append(dict(inventory_index=inventory_index,source=row['source'],path=row['path'],error=repr(exc),utc=STAMP()))
                state['next_index']+=1;progress()
                continue
            state['records'].append(record);recorded_paths.add(row['path'].casefold())
            state['next_index']+=1;progress()
        finish_part()
        complete=state['next_index']==len(state['work_queue']) and len(state['records'])==len(state['inventory']) and not state['errors'] and not any(e['reason'].startswith('INVENTORY_ERROR') for e in state['exclusions'])
        if complete:stopped='ALL_SOURCES_CAPTURED'
        elif state['next_index']==len(state['work_queue']):stopped='SOURCE_ERRORS_RETRY_NEXT_RUN' if state['errors'] else 'INVENTORY_ERRORS_REQUIRE_REVIEW'
        batch_status='BATCH_VERIFIED' if batch_parts else ('METADATA_ONLY_COMPLETION' if complete else 'NO_ARCHIVE_PARTS')
        receipt=dict(format='codex-export-batch-v1',batch=batch_no,status=batch_status,parts=batch_parts,blobs=batch_blobs,capturedFilesThisBatch=len(state['records'])-start_records,completeExport=False,allSourcesCaptured=complete,startedUtc=started,finishedUtc=STAMP(),stopReason=stopped,totalCapturedFiles=len(state['records']),totalInventoriedFiles=len(state['inventory']),captureErrors=state['errors'],note='Per-file snapshots; JSONL prefixes labeled. This receipt verifies only parts in this batch. Final full-export catalog is issued after all selected files are captured.')
        dump(batch/f'batch-{batch_no:03d}-receipt.json',receipt)
        state['next_batch']+=1;state['batches'].append(dict(batch=batch_no,receipt=str(batch/f'batch-{batch_no:03d}-receipt.json'),parts=batch_parts));dump(state_file,state);progress(True)
        if complete:
            # Metadata is uploaded alongside all accumulated ZIP parts; no joined ZIP needed.
            manifest=output/'manifest.jsonl'
            with manifest.open('x',encoding='utf-8',newline='\n') as f:
                for r in state['records']:f.write(json.dumps(r,ensure_ascii=False)+'\n')
            exclusion_path=output/'exclusions.json';dump(exclusion_path,state['exclusions'])
            catalog=dict(format='codex-project-export-v1',status='VERIFIED_WITH_EXCLUSIONS' if state['exclusions'] else 'VERIFIED',logicalBytes=sum(r['bytes'] for r in state['records']),files=len(state['records']),parts=state['parts'],blobs=state['blobs'],manifest=dict(name=manifest.name,sha256=digest(manifest)),exclusions=dict(name=exclusion_path.name,sha256=digest(exclusion_path)),captureStartedUtc=state['createdUtc'],captureFinishedUtc=STAMP(),notes='All parts verified independently at capture. Earlier batches may have been transferred off this PC. Full restore on the new PC not performed.')
            dump(output/'catalog.json',catalog)
        print(json.dumps({k:receipt[k] for k in ['batch','status','capturedFilesThisBatch','completeExport','stopReason','totalCapturedFiles','totalInventoriedFiles']},indent=2),flush=True)
        print(json.dumps(dict(parts=batch_parts,errors=len(state['errors']),batchDirectory=str(batch)),indent=2),flush=True)
    except BaseException as exc:
        if zip_obj:
            with contextlib.suppress(Exception):zip_obj.close()
        dump(batch/'INCOMPLETE-DO-NOT-UPLOAD.json',dict(error=repr(exc),time=STAMP(),note='Current partial part is not verified. Last durable checkpoint only references finalized parts. Do not treat this batch as complete.'))
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--state',required=True);p.add_argument('--output',required=True);p.add_argument('--supporting-kit');p.add_argument('--batch-bytes',type=int,default=4*1024**3);p.add_argument('--part-bytes',type=int,default=1024**3)
    run(p.parse_args())
