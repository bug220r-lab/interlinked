import collections
import csv
import hashlib
import json
import os
import stat
from pathlib import Path

BASE=Path('outputs/MAGIX-iCloud-Export-2026-10-29')
raw=BASE/'selection.json'
digest=hashlib.sha256(raw.read_bytes()).hexdigest()
if digest!='2c9e9b46aa5035ae4768f5180c48926e0181c390f3af5e2d55904a333231aca6':
    raise RuntimeError('Dependency selection changed after final audit')
spec=json.loads(raw.read_text(encoding='utf-8-sig'))
video=set('.mp4 .mov .avi .mkv .wmv .mpg .mpeg .mpe .m2v .mpv .webm .flv .f4v .3gp .3g2 .m4v .asf .ogv .qt .divx .dv .mxf .m2t .m2ts .mts .vob .vro .mxv .braw .r3d .ari .crm .cine .hevc .h264 .h265'.split())
project={'.mvp','.mvd','.mv_','.veg','.vf'}
files=[];errors=[];seen=set()
for row in spec['files']:
    if row['deferred']:continue
    p=Path(row['source']);key=str(p).casefold()
    if key in seen:raise RuntimeError('Duplicate source: '+str(p))
    seen.add(key)
    try:
        s=p.lstat()
        if not stat.S_ISREG(s.st_mode):raise RuntimeError('Not a regular file')
        if (s.st_size,s.st_mtime_ns)!=(row['bytes'],row['mtime_ns']):raise RuntimeError('File changed after dependency scan')
        for parent in (p,*p.parents):
            st=parent.lstat();attrs=getattr(st,'st_file_attributes',0);tag=getattr(st,'st_reparse_tag',0)
            cloud_dir=parent!=p and stat.S_ISDIR(st.st_mode) and (tag&0xFFFF0FFF)==0x9000001A
            if stat.S_ISLNK(st.st_mode) or (attrs&0x400 and not cloud_dir):raise RuntimeError('Unsafe reparse or link: '+str(parent))
            if parent==p and attrs&(0x1000|0x40000|0x400000):raise RuntimeError('Offline/recall file')
        if str(p)[0:3].lower()!='c:\\':raise RuntimeError('Unexpected drive')
        if p.is_relative_to(Path('.').absolute()):raise RuntimeError('Source is inside handoff workspace')
        item=dict(row);item['relativePath']='Drive_C/'+str(p)[3:].replace('\\','/')
        files.append(item)
    except Exception as exc:errors.append({'source':str(p),'error':str(exc)})
if errors:
    (BASE/'CAPTURE-SELECTION-ERRORS.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
    raise RuntimeError(f'{len(errors)} source metadata errors; freeze refused')

def priority(row):
    p=Path(row['source']);ext=p.suffix.lower()
    if ext in project:return (0,row['bytes'],str(p).casefold())
    if ext in video:return (3,row['bytes'],str(p).casefold())
    if row['category'].startswith('linked_'):return (1,row['bytes'],str(p).casefold())
    return (2,row['bytes'],str(p).casefold())
files.sort(key=priority)
scope=list(spec['coverageLimits'])+[
    'This frozen capture selection includes only 2,064 locally ready files. Twenty-one cloud/offline files remain explicitly deferred and require a later supplemental export.',
    'Reference audit: 2,870 path candidates; 338 existing paths, 732 unresolved identities with same-basename candidates included, and 1,800 candidates without a local match. These are candidate counts, not confirmed missing files.',
    'All original MVP/MV_ bytes and current source paths are preserved. No unverified media substitution or automatic relinking is performed.',
    'Files are captured as stable individual snapshots across batches, not as one simultaneous whole-library snapshot.',
    'iCloud upload, downloaded-byte verification and native MAGIX reopening/rendering are separate steps and are not established by local capture.'
]
result={'format':'magix-capture-selection-v1','sourceSelectionSha256':digest,'files':files,'excludedOrDeferred':spec['deferred'],'scopeNotes':scope}
target=BASE/'CAPTURE-SELECTION.json'
with target.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,ensure_ascii=False);f.write('\n')
with (BASE/'Cloud-deferred-files.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['source','bytes','category','status','reason'],extrasaction='ignore');writer.writeheader();writer.writerows(spec['deferred'])
audit={'sourceSelectionSha256':digest,'captureSelectionSha256':hashlib.sha256(target.read_bytes()).hexdigest(),'localFiles':len(files),'localBytes':sum(r['bytes'] for r in files),'deferredFiles':len(spec['deferred']),'projectFiles':sum(Path(r['source']).suffix.lower() in project for r in files),'categories':dict(collections.Counter(r['category'] for r in files)),'metadataErrors':errors,'order':'All projects/backups first, linked assets next, focused/candidate assets next, videos smallest first.'}
(BASE/'SELECTION-AUDIT.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
print(json.dumps(audit,indent=2))
