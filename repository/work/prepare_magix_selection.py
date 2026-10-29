"""Read project strings and metadata; select candidates without copying media."""
import collections
import csv
import datetime
import json
import ntpath
import os
from pathlib import Path
import re
import unicodedata

BASE = Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep')
PRIOR = BASE/'outputs'/'Magix-Video-Inventory-2026-10-29'
OUT = BASE/'outputs'/'MAGIX-iCloud-Export-2026-10-29'
INDEX = BASE/'work'/'magix-media-metadata.jsonl'
MAGIX = Path(r'C:\Program Files (x86)\Steam\steamapps\common\MAGIX\Video deluxe 2021 Plus Steam Edition')
AUDIO = {'.wav','.mp3','.m4a','.aac','.flac','.ogg','.aif','.aiff','.wma','.opus'}
IMAGE = {'.jpg','.jpeg','.png','.bmp','.gif','.tif','.tiff','.heic','.webp','.svg','.tga','.dds','.ico','.psd'}
VIDEO = {'.mp4','.mov','.mkv','.avi','.mts','.m2ts','.wmv','.webm','.mpg','.mpeg','.m4v','.3gp','.vob','.mxv','.m2v','.m2t'}
SIDECAR = {'.hdp','.h0','.h1','.h2','.h3','.sfk','.sfl','.tak','.mxi','.mvi','.mxm'}
PROJECT = {'.mvp','.mv_','.mvd'}
FONTS = {'.ttf','.otf'}
EXT = AUDIO|IMAGE|VIDEO|SIDECAR|PROJECT|FONTS
EXT_RE = '|'.join(re.escape(x[1:]) for x in sorted(EXT,key=len,reverse=True))
ABS_RE = re.compile(r'((?:[A-Za-z]:[\\/]|\\\\[^\\/:*?"<>|\x00-\x20]+\\)[^\x00-\x1f"<>|]{1,2048}?\.(?:'+EXT_RE+r'))(?![A-Za-z0-9_.])',re.I)
TOKEN_RE = re.compile(r'[^\x00-\x1f]{4,1024}')
REL_RE = re.compile(r'.+\.(?:'+EXT_RE+r')\Z',re.I)
BAD_ATTRS=0x400|0x1000|0x40000|0x400000
EXCLUDED_DIRS={'.git','.venv','venv','node_modules','__pycache__','cache','caches','tmp','temp'}

def key(path):
    return ntpath.normpath(str(path)).casefold()

def under(path,parent):
    a,b=key(path),key(parent)
    return a==b or a.startswith(b.rstrip('\\')+'\\')

def read_csv(name):
    with (PRIOR/name).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))

def write_json(path,obj):
    with path.open('x',encoding='utf-8') as stream:json.dump(obj,stream,ensure_ascii=False,indent=2)

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

baseline_projects=read_csv('MAGIX-projects-and-backups.csv')
baseline_videos=read_csv('Personal-videos.csv')
assert len(baseline_projects)==276 and len(baseline_videos)==537
metadata={};by_name=collections.defaultdict(dict)
with INDEX.open(encoding='utf-8-sig') as stream:
    for line in stream:
        item=json.loads(line);source=item.get('source',item.get('path'));item['source']=source
        metadata[key(source)]=item;by_name[ntpath.basename(source).casefold()][key(source)]=item

# Metadata-only built-in search; exact references/candidate names alone are selected.
builtin_index_errors=[];builtin_index_skips=[];pending=[MAGIX/'_Content']
while pending:
    directory=pending.pop()
    try:
        for item in os.scandir(directory):
            try:
                st=item.stat(follow_symlinks=False);attrs=getattr(st,'st_file_attributes',0)
                if item.is_dir(follow_symlinks=False):
                    if attrs&BAD_ATTRS or item.name.casefold() in EXCLUDED_DIRS:builtin_index_skips.append(item.path)
                    else:pending.append(Path(item.path))
                elif ntpath.splitext(item.name)[1].lower() in EXT:
                    row=dict(source=item.path,name=item.name,extension=ntpath.splitext(item.name)[1].lower(),bytes=st.st_size,mtime_ns=st.st_mtime_ns,attributes=attrs,deferred=bool(attrs&BAD_ATTRS),builtin=True)
                    metadata[key(item.path)]=row;by_name[item.name.casefold()][key(item.path)]=row
            except OSError as exc:builtin_index_errors.append(dict(path=item.path,error=str(exc)))
    except OSError as exc:builtin_index_errors.append(dict(path=str(directory),error=str(exc)))

stat_cache={}
def current_metadata(source,fallback=None):
    source=ntpath.normpath(source);k=key(source)
    if k in stat_cache:return stat_cache[k]
    out=dict(source=source)
    if not re.match(r'^[cC]:\\',source):
        out.update(status='DEFERRED_OTHER_DRIVE_OR_NETWORK',deferred=True,bytes=(fallback or {}).get('bytes'),mtime_ns=(fallback or {}).get('mtime_ns'))
    else:
        try:
            st=os.stat(source,follow_symlinks=False);attrs=getattr(st,'st_file_attributes',0)
            if os.path.isdir(source):raise OSError('Candidate is a directory, not a file')
            out.update(bytes=st.st_size,mtime_ns=st.st_mtime_ns,attributes=attrs,deferred=bool(attrs&BAD_ATTRS),status='DEFERRED_CLOUD_REPARSE_OR_OFFLINE' if attrs&BAD_ATTRS else 'LOCAL_METADATA_READY')
        except OSError as exc:
            out.update(status='UNAVAILABLE_SOURCE',deferred=True,bytes=int((fallback or {}).get('bytes') or 0),mtime_ns=(fallback or {}).get('mtime_ns'),error=str(exc))
    stat_cache[k]=out
    return out

selection={}
def add_source(source,category,reason,ref_id=None,folder=None,fallback=None):
    row=current_metadata(source,fallback);k=key(source)
    if k not in selection:selection[k]={**row,'category':category,'categories':[category],'reason':reason,'reasons':[],'reference_ids':[],'coverage_folders':[]}
    target=selection[k]
    if category not in target['categories']:target['categories'].append(category)
    if reason not in target['reasons']:target['reasons'].append(reason)
    if ref_id and ref_id not in target['reference_ids']:target['reference_ids'].append(ref_id)
    if folder and folder not in target['coverage_folders']:target['coverage_folders'].append(folder)
    return target

for row in baseline_projects:
    category='magix_project_backup' if row['path'].lower().endswith('.mv_') else 'magix_project'
    add_source(row['path'],category,'Baseline personal MAGIX project/backup inventory; preserve every variant',fallback=row)
for row in baseline_videos:add_source(row['path'],'personal_video','Baseline personal video inventory, including proxy variants',fallback=row)

refs={};project_reads=[];bytes_read=0
for i,row in enumerate(baseline_projects,1):
    p=Path(row['path']);info=current_metadata(str(p),row)
    if info['deferred']:
        project_reads.append(dict(source=str(p),status=info['status'],bytes=info['bytes']));continue
    try:
        before=p.stat();raw=p.read_bytes();after=p.stat();bytes_read+=len(raw)
        if before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns or len(raw)!=before.st_size:raise RuntimeError('Project changed during read')
        found={}
        for encoding,text in [('windows_cp1252',raw.decode('cp1252',errors='replace')),('utf16le_even',raw.decode('utf-16-le',errors='ignore')),('utf16le_odd',raw[1:].decode('utf-16-le',errors='ignore'))]:
            for match in ABS_RE.finditer(text):
                value=match.group(1).replace('/','\\').strip()
                if '\ufffd' in value:continue
                found.setdefault(('absolute',value.casefold()),{'value':value,'encodings':set()})['encodings'].add(encoding)
            for token in TOKEN_RE.finditer(text):
                value=token.group(0).strip()
                if len(value)>512 or not REL_RE.fullmatch(value) or re.search(r'[<>:"|?*]',value) or '\ufffd' in value:continue
                if value.startswith('\\\\') or re.match(r'^[A-Za-z]:',value):continue
                if any(unicodedata.category(c).startswith('C') for c in value):continue
                if any(len(part)>255 for part in re.split(r'[\\/]',value)):continue
                if not any(c.isalpha() for c in value):continue
                value=value.replace('/','\\')
                found.setdefault(('relative',value.casefold()),{'value':value,'encodings':set()})['encodings'].add(encoding)
        for (kind,k),ref in found.items():
            entry=refs.setdefault((kind,k),dict(kind=kind,value=ref['value'],observed_in=[]))
            entry['observed_in'].append(dict(project=str(p),encodings=sorted(ref['encodings'])))
        project_reads.append(dict(source=str(p),status='STABLE_PROJECT_BYTES_READ',bytes=len(raw),reference_candidates=len(found)))
    except (OSError,RuntimeError) as exc:project_reads.append(dict(source=str(p),status='READ_ERROR',error=str(exc)))
    if i%25==0:print(json.dumps(dict(phase='PROJECT_REFERENCE_SCAN',projects=i,total=len(baseline_projects),bytesRead=bytes_read,uniqueReferences=len(refs))),flush=True)

def category_for(source):
    if under(source,MAGIX):return 'linked_magix_builtin_asset'
    ext=ntpath.splitext(source)[1].lower()
    return 'linked_audio' if ext in AUDIO else 'linked_image' if ext in IMAGE else 'linked_video' if ext in VIDEO else 'linked_sidecar' if ext in SIDECAR else 'linked_project_or_asset'

resolved=[];unresolved=[];exact_media_parents=set()
project_default=r'C:\Users\jlewi\OneDrive\Documents\MAGIX\Video deluxe 2021 Plus Steam Edition'
for ordinal,entry in enumerate(sorted(refs.values(),key=lambda r:(r['kind'],r['value'].casefold())),1):
    entry['id']=f'ref-{ordinal:05d}';value=entry['value'];direct={}
    if entry['kind']=='absolute':direct[key(value)]=value
    else:
        for observation in entry['observed_in']:
            candidate=ntpath.normpath(ntpath.join(ntpath.dirname(observation['project']),value));direct[key(candidate)]=candidate
        for root in [project_default,str(MAGIX),str(MAGIX/'_Content')]:
            candidate=ntpath.normpath(ntpath.join(root,value));direct[key(candidate)]=candidate
    exact=[];deferred=[]
    for candidate in direct.values():
        info=current_metadata(candidate)
        if info['status']=='UNAVAILABLE_SOURCE':continue
        reason='Absolute path string found in project; existence verified by metadata only' if entry['kind']=='absolute' else 'Relative project-string path resolved against project/default/installed MAGIX folder; identity not content-verified'
        selected=add_source(candidate,category_for(candidate),reason,entry['id'])
        (deferred if selected['deferred'] else exact).append(candidate)
        if not selected['deferred'] and not under(candidate,MAGIX):exact_media_parents.add(ntpath.dirname(candidate))
    candidates=[]
    if not exact and not deferred:
        basename=ntpath.basename(value).casefold()
        for candidate,item in by_name.get(basename,{}).items():
            selected=add_source(item['source'],'moved_media_candidate','Same basename as unresolved project reference; candidate only, no hash/identity match or alias/relink performed',entry['id'],fallback=item)
            candidates.append({'source':item['source'],'status':selected['status'],'bytes':selected['bytes']})
    entry.update(exact_or_resolved_paths=exact,deferred_paths=deferred,same_basename_candidates=candidates)
    if exact:entry['resolution']='EXISTING_PATH_CANDIDATE_SELECTED'
    elif deferred:entry['resolution']='REFERENCED_PATH_DEFERRED'
    elif candidates:entry['resolution']='UNRESOLVED_IDENTITY_SAME_BASENAME_CANDIDATES_SELECTED'
    else:entry['resolution']='UNRESOLVED_NO_LOCAL_CANDIDATE'
    resolved.append(entry)
    if entry['resolution']!='EXISTING_PATH_CANDIDATE_SELECTED':unresolved.append(entry)

# Focused-folder coverage: project/media folders, never whole general user folders.
general={key(r'C:\Users\jlewi'),key(r'C:\Users\jlewi\Documents'),key(r'C:\Users\jlewi\Downloads'),key(r'C:\Users\jlewi\Music'),key(r'C:\Users\jlewi\Videos'),key(r'C:\Users\jlewi\Pictures'),key(r'C:\Users\jlewi\OneDrive'),key(r'C:\Users\jlewi\OneDrive\Documents'),key(r'C:\Users\jlewi\OneDrive\Desktop'),key(r'C:\Users\jlewi\OneDrive\Pictures'),key(r'C:\Users\jlewi\AppData\Roaming'),key(r'C:\Users\jlewi\iCloudDrive'),key(r'C:\Users\jlewi\OneDrive - The University of Akron')}
focused=set()
for row in baseline_projects+baseline_videos:
    parent=ntpath.dirname(row['path'])
    if key(parent) not in general:focused.add(parent)
for parent in exact_media_parents:
    if key(parent) not in general and not under(parent,MAGIX):focused.add(parent)
# Prune nested roots while preserving known folder provenance in each selected row.
focused_roots=[]
for folder in sorted(focused,key=lambda p:(len(p),p.casefold())):
    if not any(under(folder,prior) for prior in focused_roots):focused_roots.append(folder)
for item in metadata.values():
    source=item['source'];ext=ntpath.splitext(source)[1].lower()
    if ext not in AUDIO|IMAGE|SIDECAR:continue
    if under(source,MAGIX) or under(source,BASE):continue
    folder=next((f for f in focused_roots if under(source,f)),None)
    if folder:add_source(source,'focused_folder_asset','Audio/image/sidecar in a focused project or media folder; conservative relative-reference coverage, not a confirmed active dependency',folder=folder,fallback=item)

# Companion files beside directly selected sources, including general Downloads/Documents.
for source_row in list(selection.values()):
    source=source_row['source'];directory=ntpath.dirname(source);stem=ntpath.splitext(ntpath.basename(source))[0].casefold()
    for suffix in SIDECAR:
        for name in [stem+suffix,ntpath.basename(source).casefold()+suffix]:
            for item in by_name.get(name,{}).values():
                if key(ntpath.dirname(item['source']))==key(directory):add_source(item['source'],'companion_sidecar','Same-stem sidecar adjacent to selected project/media',folder=directory,fallback=item)

files=sorted(selection.values(),key=lambda r:(0 if r['category'].startswith('magix_project') else 1,r['source'].casefold()))
ready=[r for r in files if not r['deferred']];deferred_files=[r for r in files if r['deferred']]
baseline_keys={key(r['path']) for r in baseline_projects+baseline_videos}
assert baseline_keys.issubset(selection)
OUT.mkdir(parents=True,exist_ok=True)
coverage_limits=[
 'Selection uses path strings from proprietary MAGIX project/backup files, not a complete semantic dependency parser. Relative/encoded/compressed references can be missed.',
 'Same-basename candidates retain their current paths and are not claimed to be byte-identical replacements. No project was edited, relinked or aliased.',
 'Focused folder assets intentionally include possible unused audio/images/sidecars to cover relative links; none are claimed to be active timeline dependencies.',
 'No media payload was read or hashed; only project/backup bytes were read. Project stability checked by size and mtime; no MAGIX reopen/render test was performed.',
 'Five baseline cloud-flagged videos and any additional unavailable/cloud/reparse references remain explicit deferred entries. Nothing was hydrated or downloaded.',
 'C:\\Users media-name index omits application/cache roots, links and inaccessible paths as recorded by magix-media-metadata-summary.json. Exact referenced AppData/installed assets were checked separately.',
 'Installed MAGIX assets are selected only when explicit references or unresolved same-basename candidates support them. The MAGIX application, codecs, plug-ins and licensing are not a portable installation.',
 'Unknown relative references and missing historical literal paths remain unresolved; their absence does not prove media loss.'
]
summary=dict(generatedUtc=stamp(),baselineProjects=len(baseline_projects),baselineVideos=len(baseline_videos),baselineCoverageEntries=len(baseline_keys),projectBytesRead=bytes_read,projectReadStatusCounts=dict(collections.Counter(r['status'] for r in project_reads)),referenceCandidates=len(resolved),referenceResolutionCounts=dict(collections.Counter(r['resolution'] for r in resolved)),selectedFiles=len(files),localReadyFiles=len(ready),localReadyBytes=sum(r['bytes'] for r in ready),deferredFiles=len(deferred_files),deferredKnownBytes=sum(r['bytes'] or 0 for r in deferred_files),categoryCounts=dict(collections.Counter(r['category'] for r in files)),focusedFolderCount=len(focused_roots),builtinMediaIndexFiles=sum(bool(r.get('builtin')) for r in metadata.values()),builtinIndexErrors=len(builtin_index_errors))
write_json(OUT/'selection.json',dict(format='magix-file-selection-v1',summary=summary,files=files,deferred=deferred_files,coverageLimits=coverage_limits,relativePathMapping='Not assigned; root capture pipeline will map original paths under Drive_C without modifying projects.'))
write_json(OUT/'dependency-report.json',dict(summary=summary,method='Windows cp1252 and UTF-16LE at both byte alignments; absolute paths plus plausible null-delimited relative media/project/sidecar/font strings. Metadata only for candidates.',projectReads=project_reads,references=resolved,focusedFolders=focused_roots,metadataIndex=str(INDEX),builtinIndexErrors=builtin_index_errors,builtinIndexSkipped=builtin_index_skips,coverageLimits=coverage_limits))
write_json(OUT/'unresolved-references.json',dict(summary=summary,references=unresolved,coverageLimits=coverage_limits))
with (OUT/'unresolved-references.csv').open('x',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['reference_id','kind','reference','resolution','projects_count','project_examples','deferred_paths','same_basename_candidates']);writer.writeheader()
    for r in unresolved:writer.writerow(dict(reference_id=r['id'],kind=r['kind'],reference=r['value'],resolution=r['resolution'],projects_count=len(r['observed_in']),project_examples=' | '.join(x['project'] for x in r['observed_in'][:5]),deferred_paths=' | '.join(r['deferred_paths']),same_basename_candidates=' | '.join(x['source'] for x in r['same_basename_candidates'])))
print(json.dumps(summary,indent=2),flush=True)
