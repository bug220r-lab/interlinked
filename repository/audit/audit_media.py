"""Read-only MAGIX reference audit from persisted evidence and bounded metadata.

No project/media payload reads, hydration, application launch, catalog change,
cache change, network access or automatic repair. Outputs are private metadata.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import stat

OLD = Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep')
HERE = Path(__file__).resolve().parent
ROOTS = ('OneDrive/Documents', 'OneDrive/Pictures', 'OneDrive/Desktop', 'Downloads',
         'OneDrive - The University of Akron/The Rialto Theatre',
         'AppData/Roaming/MAGIX/Videodeluxe27_plus_steam')
PREFIX = 'Drive_C/Users/jlewi/'
ENGINE_SHA = '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d'
BAD = 0x400 | 0x1000 | 0x40000 | 0x400000
SIDECARS = {'.hdp', '.h0', '.h1', '.h2', '.h3', '.sfk', '.sfl', '.mxi', '.mvi', '.mxm'}
PROJECTS = {'.mvp', '.mv_', '.mvd'}
MEDIA = set('.mp4 .mov .mkv .avi .mts .m2ts .wmv .webm .mpg .mpeg .m4v .3gp .vob .mxv .m2v .m2t .wav .mp3 .m4a .aac .flac .ogg .aif .aiff .wma .opus .jpg .jpeg .png .bmp .gif .tif .tiff .heic .webp .svg .tga .dds .ico .psd'.split())
LIMITS = [
    'References are strings previously extracted from proprietary project/backup files, not a parsed active timeline dependency graph.',
    'All project variants and backups are audited separately. Old/deleted/import-history strings can remain in a project file.',
    'An existing path or relative-path candidate does not prove that MAGIX currently uses that media. Missing strings do not prove an active clip is offline.',
    'Same-basename alternatives remain unverified even when unique. Matching a candidate to its own published hardlink does not identify the missing original.',
    'Source and published-library checks use metadata and file IDs only. No media bytes were hashed and codec compatibility was not tested.',
    'Current source/published hardlink identity does not prove the file still contains the media version present when the project was authored.',
    'Relative strings are checked against persisted candidates and inferred project-parent paths only as search candidates. MAGIX relative-path resolution semantics were not established.',
    'Actual new-PC cache contents were not inspected. Catalog coverage describes what a clean fetch selects, not an edited READY cache.',
    'Cloud-only, reparse and inaccessible files remain deferred; no downloads or hydration were requested.',
    'Installed MAGIX assets are not mapped into Program Files. Compatible installed assets or a supported explicit relink can still be required.',
    'No new broad filesystem search was performed. Previously indexed basename alternatives and bounded literal/relative paths were rechecked.',
]

def stamp(): return datetime.now(timezone.utc).isoformat()
def key(path): return ntpath.normpath(str(path)).casefold()
def digest(data): return hashlib.sha256(data).hexdigest()
def absolute(relative):
    p=relative.removeprefix('Files/').split('/')
    if not p or not re.fullmatch(r'Drive_[A-Z]',p[0]): raise ValueError('Unexpected catalog path')
    return str(PureWindowsPath(p[0][-1]+':/',*p[1:]))
def relative(source):
    p=PureWindowsPath(ntpath.normpath(source))
    return 'Drive_'+p.drive[0].upper()+'/'+'/'.join(p.parts[1:])
def alias_root(path):
    path=path.removeprefix('Files/')
    for root in ROOTS:
        if path.casefold().startswith((PREFIX+root+'/').casefold()): return root
    return None
def builtin(path): return key(path).startswith(r'c:\program files (x86)\steam\steamapps\common\magix'+'\\')
def role(path):
    suffix=ntpath.splitext(path)[1].lower()
    return 'sidecar' if suffix in SIDECARS else 'project_or_backup' if suffix in PROJECTS else 'media_or_asset'

class Metadata:
    def __init__(self): self.files={};self.parents={};self.calls=0
    def parent_check(self,path):
        token=key(path)
        if token in self.parents:return self.parents[token]
        if path.parent!=path:
            previous=self.parent_check(path.parent)
            if previous:self.parents[token]=previous;return previous
        try:
            s=path.lstat();a=getattr(s,'st_file_attributes',0);tag=getattr(s,'st_reparse_tag',0)
            cloud=stat.S_ISDIR(s.st_mode) and (tag&0xffff0fff)==0x9000001a
            result=None if stat.S_ISDIR(s.st_mode) and (not(a&0x400) or cloud) else 'UNSAFE_OR_NON_DIRECTORY_PARENT'
        except FileNotFoundError:result='MISSING_PARENT'
        except OSError:result='INACCESSIBLE_PARENT'
        self.parents[token]=result;return result
    def get(self,source):
        token=key(source)
        if token in self.files:return self.files[token]
        if len(self.files)>=25000:raise RuntimeError('Bounded metadata path limit exceeded')
        out=dict(path=str(source),status='INVALID_OR_UNSUPPORTED_PATH')
        self.files[token]=out
        # Do not access a UNC host, device path, alternate stream or other drive.
        norm=ntpath.normpath(str(source));drive,tail=ntpath.splitdrive(norm)
        if drive.casefold()!='c:' or not tail.startswith('\\') or any(c in tail for c in '\x00<>|?*"') or ':' in tail:return out
        path=Path(norm);parent_error=self.parent_check(path.parent)
        if parent_error:
            out['status']='MISSING' if parent_error=='MISSING_PARENT' else parent_error;return out
        try:
            self.calls+=1;s=path.lstat();attrs=getattr(s,'st_file_attributes',0)
            out.update(path=norm,bytes=s.st_size,mtime_ns=s.st_mtime_ns,attributes=attrs,fileId=str(s.st_ino),volumeId=str(s.st_dev))
            out['status']='DEFERRED_CLOUD_OR_REPARSE' if attrs&BAD else 'LOCAL_AVAILABLE' if stat.S_ISREG(s.st_mode) else 'NOT_REGULAR_FILE'
        except FileNotFoundError:out['status']='MISSING'
        except OSError as exc:out.update(status='INACCESSIBLE',errorCode=getattr(exc,'winerror',None))
        return out

def available(info):return info['status']=='LOCAL_AVAILABLE'
def file_identity(a,b):return available(a) and available(b) and a.get('fileId') not in ('0',None) and (a.get('fileId'),a.get('volumeId'))==(b.get('fileId'),b.get('volumeId'))
def metadata_matches(info,row):return available(info) and (info['bytes'],info['mtime_ns'])==(row['bytes'],row['mtime_ns'])
def clean_relative(value):
    return bool(value) and not ntpath.isabs(value) and not ntpath.splitdrive(value)[0] and not any(c in value for c in '\x00<>|?*":') and len(value)<2048

def output_inside_source(output,source_roots):
    value=key(output)
    return any(value==key(root) or value.startswith(key(root).rstrip('\\')+'\\') for root in source_roots)

def candidate_locations(ref,filemap):
    """Concrete fetch-relative suggestions; never claim edited cache existence."""
    rows=[]
    for item in ref['sameBasenameCandidates']:
        path=item['catalogPath'];selected=filemap.get(path.casefold())
        cache='Files/'+path if selected else None
        rows.append(dict(sourcePath=item['path'],matchBasis='BASENAME_ONLY_IDENTITY_UNVERIFIED',sourceStatus=item['metadata']['status'],selectedForFamily=selected is not None,libraryMetadataStatus=selected['libraryMetadata']['status'] if selected else None,libraryMatchesCatalog=selected['libraryMatchesCatalog'] if selected else False,cacheRelativePath=cache,cacheFolderRelativePath=cache.rsplit('/',1)[0] if cache else None,currentClientCacheExists='NOT_CHECKED',use='Optional folder to review in MAGIX after a local cache existence check and content confirmation; not an automatic substitute.'))
    return rows

def classify_reference(ref, paths, candidates):
    current=[p for p in paths if available(p['metadata'])]
    deferred=[p for p in paths if p['metadata']['status'].startswith('DEFERRED')]
    options=[p for p in candidates if available(p['metadata'])]
    if current:
        if ref['kind']=='relative':return 'RELATIVE_PATH_CANDIDATE_AVAILABLE'
        if any(p['installedAsset'] for p in current):return 'EXACT_INSTALLED_ASSET_AVAILABLE'
        return 'EXACT_LITERAL_PATH_AVAILABLE'
    if deferred:return 'DEFERRED_CLOUD_OR_REPARSE'
    if len(options)>1:return 'MULTIPLE_BASENAME_CANDIDATES_UNVERIFIED'
    if len(options)==1:return 'SINGLE_BASENAME_CANDIDATE_UNVERIFIED'
    if any(p['metadata']['status'].startswith('DEFERRED') for p in candidates):return 'BASENAME_CANDIDATE_DEFERRED'
    if not paths:return 'INVALID_OR_UNSUPPORTED_REFERENCE'
    return 'NO_LOCAL_MATCH_IN_AUDITED_PATHS'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=OLD/'outputs/Project-Library-2026-10-29/HostLibrary')
    parser.add_argument('--dependencies',type=Path,default=OLD/'outputs/MAGIX-iCloud-Export-2026-10-29/dependency-report.json')
    parser.add_argument('--selection',type=Path,default=OLD/'outputs/MAGIX-iCloud-Export-2026-10-29/selection.json')
    parser.add_argument('--engine',type=Path,default=OLD/'outputs/Project-Library-2026-10-29/Helper/project_library.py')
    parser.add_argument('--output',type=Path,required=True,help='New private report directory outside source folders, normally the ignored workspace work/ directory.')
    args=parser.parse_args();began=stamp();out=args.output.absolute()
    for control in (args.library,args.dependencies,args.selection,args.engine,out):
        if str(control).startswith('\\\\'):raise RuntimeError('Audit inputs and output must be local, not UNC paths')
    source_roots=[HERE]
    source_roots.extend(parent for parent in HERE.parents if (parent/'SOURCE-MANIFEST.json').is_file())
    if output_inside_source(out.resolve(),[p.resolve() for p in source_roots]):
        raise RuntimeError('Private report output must be outside the source distribution/code directory; use an ignored work/ or external private directory.')
    names=['MAGIX-media-audit.json','MAGIX-media-audit-summary.json','MAGIX-media-audit-gui.json','MAGIX-media-audit.html']
    if any((out/name).exists() for name in names):raise RuntimeError('Refuse to overwrite an audit report')
    raw=args.engine.read_bytes()
    if digest(raw)!=ENGINE_SHA:raise RuntimeError('Reviewed reader engine differs')
    spec=importlib.util.spec_from_file_location('audit_reader_engine',args.engine);engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    provenance={}
    def read(name,path):
        raw=path.read_bytes();provenance[name]=dict(path=str(path),bytes=len(raw),sha256=digest(raw));return json.loads(raw.decode('utf-8-sig'))
    deps=read('dependencyReport',args.dependencies);selection=read('selection',args.selection)
    metadata=Metadata();selected={key(r['source']):r for r in selection['files']}
    files={};projects=[];references=[];fixes=[]
    with engine.reader(args.library/'.library-access.lock'):
        cat=read('publishedCatalog',args.library/'catalog.json')
        families=[p for p in cat['projects'] if p.get('category')=='magix-project']
        assert len(families)==43 and sum(len(p['entryPoints']) for p in families)==276
        for family in families:
            for row in family['files']:
                token=row['path'].casefold()
                if token in files:continue
                source=absolute(row['path']);original=metadata.get(source);published=metadata.get(str(args.library/row['sourceRelative']))
                files[token]=dict(path=row['path'],source=source,sourceRelative=row['sourceRelative'],expectedBytes=row['bytes'],expectedMtimeNs=row['mtime_ns'],sourceMetadata=original,libraryMetadata=published,samePhysicalFile=file_identity(original,published),sourceMatchesCatalog=metadata_matches(original,row),libraryMatchesCatalog=metadata_matches(published,row),aliasRoot=alias_root(row['path']),installedAsset=builtin(source))
        print(json.dumps(dict(phase='SELECTED_METADATA_CHECKED',uniqueSelectedFiles=len(files),sourceAvailable=sum(available(f['sourceMetadata']) for f in files.values()),libraryAvailable=sum(available(f['libraryMetadata']) for f in files.values()))),flush=True)
        refs_by_id={r['id']:r for r in deps['references']}
        family_by_ref=defaultdict(list);family_by_source=defaultdict(list)
        for family in families:
            for rid in family['dependencyProvenance']['referenceIds']:family_by_ref[rid].append(family['id'])
            for source in family['dependencyProvenance']['projectSources']:family_by_source[key(source)].append(family['id'])
        ref_lookup={}
        for rid in sorted(family_by_ref):
            ref=refs_by_id[rid];direct={key(p):p for p in ref['exact_or_resolved_paths']+ref['deferred_paths']}
            persisted_keys=set(direct)
            if ref['kind']=='absolute':direct[key(ref['value'])]=ref['value']
            elif clean_relative(ref['value']):
                for obs in ref['observed_in']:
                    path=ntpath.normpath(ntpath.join(ntpath.dirname(obs['project']),ref['value']));direct[key(path)]=path
            paths=[]
            for path in direct.values():
                info=metadata.get(path);mapped=relative(path) if ntpath.splitdrive(path)[0].lower()=='c:' else None
                f=files.get(mapped.casefold()) if mapped else None
                basis='EXACT_ABSOLUTE_LITERAL' if ref['kind']=='absolute' and key(path)==key(ref['value']) else 'PERSISTED_RELATIVE_RESOLUTION_CANDIDATE' if key(path) in persisted_keys else 'INFERRED_PROJECT_PARENT_SEARCH_CANDIDATE'
                paths.append(dict(path=path,metadata=info,catalogPath=mapped,publishedInAnyAuditedFamily=f is not None,aliasRoot=alias_root(mapped) if mapped else None,installedAsset=builtin(path),resolutionBasis=basis))
            candidates=[]
            for candidate in ref['same_basename_candidates']:
                path=candidate['source'];mapped=relative(path);f=files.get(mapped.casefold())
                candidates.append(dict(path=path,metadata=metadata.get(path),catalogPath=mapped,publishedInAnyAuditedFamily=f is not None,identityToMissingOriginal='UNVERIFIED',priorStatus=candidate['status']))
            classification=classify_reference(ref,paths,candidates)
            item=dict(id=rid,kind=ref['kind'],path=ref['value'],name=ntpath.basename(ref['value']),classification=classification,role=role(ref['value']),qualifiedMedia=ntpath.splitext(ref['value'])[1].lower() in MEDIA,historicalResolution=ref['resolution'],observedIn=ref['observed_in'],projectIds=family_by_ref[rid],exactOrResolvedPaths=paths,sameBasenameCandidates=candidates,activeDependency='UNKNOWN',contentIdentityToMissingOriginal='NOT_PROVEN',proposedFix=None)
            references.append(item);ref_lookup[rid]=item
        for family in families:
            filemap={f['path'].casefold():files[f['path'].casefold()] for f in family['files']}
            localrefs=[];counts=Counter();ids=family['dependencyProvenance']['referenceIds'];fix_ids=[]
            for rid in ids:
                ref=ref_lookup[rid];matching=[p for p in ref['exactOrResolvedPaths'] if available(p['metadata'])]
                within=[p for p in matching if p.get('catalogPath','').casefold() in filemap]
                stable=[p for p in within if filemap[p['catalogPath'].casefold()]['libraryMatchesCatalog']]
                mapped=[p for p in stable if p['aliasRoot']]
                status=ref['classification'];fix=None;locations=candidate_locations(ref,filemap)
                if ref['kind']=='absolute' and matching:
                    if ref['classification']=='EXACT_INSTALLED_ASSET_AVAILABLE':status='INSTALLED_ASSET_REQUIRES_CLIENT_COMPATIBILITY'
                    elif mapped:status='EXACT_PATH_SELECTED_LIBRARY_READY_SUPPORTED_ALIAS'
                    elif within and not stable:status='EXACT_PATH_SELECTED_LIBRARY_METADATA_DRIFT'
                    elif stable:status='EXACT_PATH_SELECTED_UNSUPPORTED_ALIAS_ROOT'
                    else:status='EXACT_PATH_AVAILABLE_NOT_SELECTED_FOR_FAMILY'
                if status=='EXACT_PATH_AVAILABLE_NOT_SELECTED_FOR_FAMILY':
                    # Eligibility for a separate guarded snapshot/copy, never a binary edit.
                    eligible=[p for p in matching if p['aliasRoot'] and not p['installedAsset']]
                    if eligible:
                        fix=dict(id=family['id']+'--'+rid,type='ADD_EXACT_LITERAL_FILE_TO_WORKING_SET',automaticSafe=False,eligibleForGuardedExactPathCopy=True,path=eligible[0]['path'],referenceIds=[rid],projectIds=[family['id']],reason='An exact literal path currently exists but this family does not select it. Separate copy-time validation, size approval and owned-cache guard required; no original project edit.',preconditions=['Close MAGIX and hold owned cache/workflow locks','Revalidate source metadata and hash while copying','Never replace an existing edited cached file','Do not change published catalog in place'])
                elif status in ('SINGLE_BASENAME_CANDIDATE_UNVERIFIED','MULTIPLE_BASENAME_CANDIDATES_UNVERIFIED'):
                    fix=dict(id=family['id']+'--'+rid,type='REVIEW_CANDIDATE_IN_MAGIX',automaticSafe=False,eligibleForGuardedExactPathCopy=False,referenceIds=[rid],projectIds=[family['id']],candidatePaths=locations,reason='Filename similarity cannot identify the missing original; choose only after content/editor confirmation and preserve a project copy.')
                if fix:fixes.append(fix);fix_ids.append(fix['id'])
                counts[status]+=1
                localrefs.append(dict(id=rid,kind=ref['kind'],path=ref['path'],name=ref['name'],classification=status,status=status,role=ref['role'],qualifiedMedia=ref['qualifiedMedia'],candidatePaths=locations,evidence=dict(extractedStringOnly=True,activeDependency='UNKNOWN',availablePathCandidates=len(matching),selectedPathCandidates=len(within),selectedLibraryStableCandidates=len(stable),selectedSupportedAliasCandidates=len(mapped),basenameCandidates=len(ref['sameBasenameCandidates'])),proposedFix=fix['id'] if fix else None))
            local_lookup={r['id']:r for r in localrefs};entrypoints=[]
            for entry in family['entryPoints']:
                source=absolute(entry['path']);observed=[rid for rid in ids if any(key(o['project'])==key(source) for o in ref_lookup[rid]['observedIn'])]
                entrypoints.append(dict(path=entry['path'],source=source,type=entry['type'],sourceMetadata=metadata.get(source),referenceIds=observed,counts=dict(Counter(local_lookup[rid]['classification'] for rid in observed)),mediaCounts=dict(Counter(local_lookup[rid]['classification'] for rid in observed if local_lookup[rid]['qualifiedMedia'])),activeDependencyGraph='NOT_PARSED'))
            selected_rows=list(filemap.values());coverage=dict(selectedFiles=len(selected_rows),selectedBytes=sum(f['expectedBytes'] for f in selected_rows),sourceAvailable=sum(available(f['sourceMetadata']) for f in selected_rows),libraryAvailable=sum(available(f['libraryMetadata']) for f in selected_rows),samePhysicalSourceAndLibrary=sum(f['samePhysicalFile'] for f in selected_rows),sourceMatchesCatalog=sum(f['sourceMatchesCatalog'] for f in selected_rows),libraryMatchesCatalog=sum(f['libraryMatchesCatalog'] for f in selected_rows),supportedAliasFiles=sum(bool(f['aliasRoot']) for f in selected_rows),installedAssetFiles=sum(f['installedAsset'] for f in selected_rows),clientCacheStatus='NOT_INSPECTED',cacheCoverageMeaning='Catalog selection and current host library metadata; an edited client cache must be checked locally.')
            blockers=coverage['libraryMatchesCatalog']!=coverage['selectedFiles']
            folders=defaultdict(list)
            for ref in localrefs:
                if not ref['qualifiedMedia']:continue
                for location in ref['candidatePaths']:
                    if location['cacheFolderRelativePath'] and location['libraryMatchesCatalog']:
                        folders[location['cacheFolderRelativePath']].append(ref['id'])
            folder_suggestions=[dict(cacheFolderRelativePath=path,referenceIds=sorted(set(ids)),matchBasis='UNVERIFIED_BASENAME_ALTERNATIVES',currentClientCacheExists='NOT_CHECKED',automaticRelink=False) for path,ids in sorted(folders.items())]
            project=dict(id=family['id'],name=family['name'],health='HOST_LIBRARY_REVIEW_REQUIRED' if blockers else 'REFERENCE_REVIEW_REQUIRED',status='HOST_LIBRARY_REVIEW_REQUIRED' if blockers else 'REFERENCE_REVIEW_REQUIRED',summary='Reference strings and fetch coverage audited; actual project opening/playback unverified.',counts=dict(referenceCandidates=len(ids),mediaReferenceCandidates=sum(r['qualifiedMedia'] for r in localrefs),entrypoints=len(entrypoints),classifications=dict(counts),mediaClassifications=dict(Counter(r['classification'] for r in localrefs if r['qualifiedMedia']))),coverage=coverage,entryPoints=entrypoints,references=localrefs,referenceIds=ids,proposedFixes=fix_ids,candidateFolderSuggestions=folder_suggestions,warnings=list(family.get('warnings',[]))+['Counts describe historical reference candidates, not active missing clips.'],deferredSources=family['dependencyProvenance'].get('deferredSources',[]))
            projects.append(project)
        # Recheck only observed available files to expose concurrent change during audit.
        unstable=[]
        for token,info in metadata.files.items():
            if not available(info):continue
            try:
                now=Path(info['path']).lstat()
                if (now.st_size,now.st_mtime_ns,str(now.st_ino),str(now.st_dev),getattr(now,'st_file_attributes',0))!=(info['bytes'],info['mtime_ns'],info['fileId'],info['volumeId'],info['attributes']):unstable.append(info['path'])
            except OSError:unstable.append(info['path'])
    deferred=[dict(source=r['source'],priorStatus=r['status'],metadata=metadata.get(r['source']),relatedProjectIds=[p['id'] for p in projects if key(r['source']) in {key(x) for x in p['deferredSources']}]) for r in selection['deferred']]
    summary=dict(projectFamilies=len(projects),entrypoints=sum(len(p['entryPoints']) for p in projects),uniqueReferenceCandidates=len(references),referenceClassifications=dict(Counter(r['classification'] for r in references)),familyReferenceClassifications=dict(sum((Counter(p['counts']['classifications']) for p in projects),Counter())),uniqueSelectedFiles=len(files),uniqueSelectedBytes=sum(f['expectedBytes'] for f in files.values()),sourceAvailable=sum(available(f['sourceMetadata']) for f in files.values()),libraryAvailable=sum(available(f['libraryMetadata']) for f in files.values()),sourceLibrarySamePhysicalFile=sum(f['samePhysicalFile'] for f in files.values()),sourceMatchesCatalog=sum(f['sourceMatchesCatalog'] for f in files.values()),libraryMatchesCatalog=sum(f['libraryMatchesCatalog'] for f in files.values()),metadataPathsChecked=len(metadata.files),metadataFileQueries=metadata.calls,filesChangedDuringAudit=len(unstable),priorGlobalCloudDeferredFiles=len(deferred),familyRelatedCloudDeferredFiles=sum(bool(r['relatedProjectIds']) for r in deferred),proposedFixes=len(fixes),exactLiteralAdditionsEligibleForSeparateReview=sum(f.get('eligibleForGuardedExactPathCopy',False) for f in fixes),automaticRepairsPerformed=0,contentHashesComputed=0)
    summary['qualifiedMediaReferenceCandidates']=sum(r['qualifiedMedia'] for r in references)
    summary['mediaReferenceClassifications']=dict(Counter(r['classification'] for r in references if r['qualifiedMedia']))
    result=dict(format='magix-media-audit-v1',generatedUtc=stamp(),startedUtc=began,catalogId=cat['catalogId'],summary=summary,scope='43 MAGIX families and every 276 persisted project/backup entrypoints; read-only host metadata and persisted extracted reference strings.',limitations=LIMITS,provenance=provenance,projects=projects,references=references,files=list(files.values()),proposedFixes=fixes,deferred=deferred,unstablePaths=unstable,actions=dict(projectPayloadsRead=False,mediaPayloadsRead=False,hydrationRequested=False,networkAccess=False,originalFilesModified=False,catalogModified=False,clientCacheModified=False,repairsPerformed=False))
    out.mkdir(parents=True,exist_ok=True)
    gui=dict(format='magix-media-audit-gui-v1',generatedUtc=result['generatedUtc'],catalogId=result['catalogId'],summary=summary,scope=result['scope'],limitations=LIMITS,provenance=provenance,projects=[dict(id=p['id'],name=p['name'],health=p['health'],status=p['status'],summary=p['summary'],counts=p['counts'],coverage=p['coverage'],entryPoints=[{k:v for k,v in e.items() if k not in ('sourceMetadata',)} for e in p['entryPoints']],warnings=p['warnings'],references=[r for r in p['references'] if r['qualifiedMedia']],proposedFixes=p['proposedFixes'],candidateFolderSuggestions=p['candidateFolderSuggestions'],deferredSources=p['deferredSources']) for p in projects],proposedFixes=fixes)
    for name,value in [('MAGIX-media-audit.json',result),('MAGIX-media-audit-summary.json',dict(format=result['format'],generatedUtc=result['generatedUtc'],summary=summary,limitations=LIMITS)),('MAGIX-media-audit-gui.json',gui)]:
        with (out/name).open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
    document=render_html(result)
    with (out/'MAGIX-media-audit.html').open('x',encoding='utf-8') as stream:stream.write(document)
    print(json.dumps(dict(summary=summary,report=str(out/'MAGIX-media-audit.json'),sha256=digest((out/'MAGIX-media-audit.json').read_bytes()))),flush=True)

def render_html(data):
    esc=html.escape;blocks=[]
    for p in data['projects']:
        rows=''.join('<tr><td>'+esc(r['name'])+'</td><td>'+esc(r['classification'])+'</td><td>'+esc(r['role'])+'</td><td><code>'+esc(r['path'])+'</code></td><td>'+''.join('<code>'+esc(c['cacheRelativePath'] or c['sourcePath'])+'</code><br>' for c in r['candidatePaths'])+'</td></tr>' for r in p['references'])
        counts=' · '.join(f'{esc(k)}: {v}' for k,v in p['counts']['classifications'].items())
        blocks.append('<details class="project" data-name="'+esc(p['name'].casefold(),quote=True)+'"><summary>'+esc(p['name'])+' — '+str(p['counts']['referenceCandidates'])+' candidate strings</summary><p>'+counts+'</p><p>Published files matching catalog: '+str(p['coverage']['libraryMatchesCatalog'])+'/'+str(p['coverage']['selectedFiles'])+'. Client cache not inspected. Candidate paths below are unverified basename alternatives; check actual local existence and media content before using MAGIX relink.</p><table><tr><th>Name</th><th>Evidence category</th><th>Role</th><th>Extracted reference</th><th>Unverified candidate location relative to selected project cache</th></tr>'+rows+'</table></details>')
    s=data['summary']
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>MAGIX media audit</title><style>body{background:#07110c;color:#c5e6cf;font:15px system-ui;margin:32px;max-width:1500px}h1,h2,summary{color:#8af5a8}summary{cursor:pointer;padding:15px;border:1px solid #285a37}details{margin:12px 0}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #20482e;padding:8px;overflow-wrap:anywhere}code{white-space:pre-wrap}input{padding:10px;background:#102019;color:#fff;border:1px solid #5ba16e;width:400px}.notice{border-left:4px solid #e4c565;padding:12px;background:#172219}a{color:#9bedb1}</style><h1>MAGIX media audit</h1><p>'+esc(data['generatedUtc'])+'</p><p>'+str(s['projectFamilies'])+' families · '+str(s['entrypoints'])+' project/backup entries · '+str(s['uniqueReferenceCandidates'])+' distinct candidate strings</p><p class="notice">These are historical extracted strings, not confirmed active missing clips. No original files, links, media or caches were changed. This report does not certify that every project opens.</p><p>Fresh host metadata: '+str(s['libraryMatchesCatalog'])+'/'+str(s['uniqueSelectedFiles'])+' selected files match the catalog. '+str(s['sourceLibrarySamePhysicalFile'])+' source/library pairs share the same physical file identity. No media content was hashed.</p><input id="filter" placeholder="Filter project families" aria-label="Filter project families">'+''.join(blocks)+'<h2>Limits</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in data['limitations'])+'</ul><script>document.getElementById("filter").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll(".project").forEach(p=>p.hidden=!p.dataset.name.includes(q));});</script></html>'

if __name__=='__main__':main()
