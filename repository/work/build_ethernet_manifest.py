"""Build a current metadata-only Ethernet transfer selection. Never copies payloads."""
from __future__ import annotations

import collections
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import stat
import sys

BASE = Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep')
OUT = BASE/'outputs'/'Ethernet-Transfer-2026-10-29'/'Copy-Helper'
PLAN = BASE/'outputs'/'New-PC-Handoff-2026-10-28'/'transfer-plan.json'
MAGIX_ROOT = BASE/'outputs'/'MAGIX-iCloud-Export-2026-10-29'
MAGIX_SELECTION = MAGIX_ROOT/'CAPTURE-SELECTION.json'
OLD_STATE = BASE/'work'/'full-export-state'/'checkpoint.json'
sys.path.insert(0,str(BASE/'work'/'full-export-tools'))
import capture_export

if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8')

def stamp():return datetime.now(timezone.utc).isoformat()
def canonical(source):return ntpath.normpath(os.path.abspath(source)).casefold()
def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:return json.load(stream)
def write_json_new(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
def mapping(source):
    p=PureWindowsPath(ntpath.normpath(os.path.abspath(source)))
    if p.drive.upper()!='C:' or not p.root:raise RuntimeError('Expected an absolute C: source: '+source)
    return capture_export.safe_relative('Drive_C/'+'/'.join(p.parts[1:]))
def source_kind(source):
    if canonical(source)==canonical(str(capture_export.ACTIVE_DB)):return 'sqlite'
    if source.lower().endswith('.jsonl') and any(capture_export.under(source,p) for p in [r'C:\Users\jlewi\.codex\sessions',r'C:\Users\jlewi\.codex\archived_sessions']):return 'jsonl'
    return 'file'

def inspect_source(source,fallback=None):
    source=ntpath.normpath(os.path.abspath(source))
    try:
        st=Path(source).lstat()
        reason=capture_export.file_reason(Path(source),st)
        if not stat.S_ISREG(st.st_mode):reason=reason or 'Source is not a regular file'
        row=dict(source=source,relativePath=mapping(source),bytes=st.st_size,mtime_ns=st.st_mtime_ns,attributes=getattr(st,'st_file_attributes',0))
        return row,reason
    except (OSError,RuntimeError) as exc:
        fallback=fallback or {}
        return dict(source=source,relativePath=mapping(source),bytes=fallback.get('bytes'),mtime_ns=fallback.get('mtime_ns'),attributes=None),'Current source unavailable: '+str(exc)

def build():
    manifest_path=OUT/'transfer-manifest.json';report_path=OUT/'MANIFEST-BUILD-REPORT.json'
    if manifest_path.exists() or report_path.exists():raise RuntimeError('Refusing to overwrite frozen manifest/report')
    began=stamp();plan=read_json(PLAN);magix=read_json(MAGIX_SELECTION)
    if magix.get('format')!='magix-capture-selection-v1':raise RuntimeError('Unexpected frozen MAGIX selection')
    current,current_exclusions=capture_export.enumerate_sources(plan,OUT)
    print(json.dumps(dict(phase='CURRENT_PROJECT_ENUMERATION',files=len(current),bytes=sum(r['bytes'] for r in current),exclusions=len(current_exclusions))),flush=True)
    entries=[e for e in plan['entries'] if e.get('enabled') is True]
    chosen={};deferred={};exclusions=[];duplicate_sources=0;magix_changes=[]
    def add_deferred(row,reason,scopes,origin,subtree=False):
        ident=canonical(row['source']);entry=deferred.setdefault(ident,{**row,'reason':reason,'reasons':[],'scopes':[],'origins':[],'subtree':subtree})
        for value in scopes:
            if value not in entry['scopes']:entry['scopes'].append(value)
        if reason not in entry['reasons']:entry['reasons'].append(reason)
        if origin not in entry['origins']:entry['origins'].append(origin)
    def add_candidate(source,scopes,fallback=None,origin=None,category=None):
        nonlocal duplicate_sources
        row,reason=inspect_source(source,fallback)
        if reason:
            add_deferred(row,reason,scopes,origin or 'current_metadata');return
        if canonical(source) in deferred:
            # Deferred frozen MAGIX rows are intentionally not promoted by a second scope.
            add_deferred(row,'Previously deferred source remains held for explicit supplemental handling',scopes,origin or 'current_metadata');return
        ident=canonical(source)
        if ident in chosen:
            duplicate_sources+=1;existing=chosen[ident]
            if existing['relativePath'].casefold()!=row['relativePath'].casefold():raise RuntimeError('Source has inconsistent destination mapping')
            if (existing['bytes'],existing['mtime_ns'])!=(row['bytes'],row['mtime_ns']):raise RuntimeError('Source changed between overlapping selection reads: '+source)
        else:
            chosen[ident]=dict(source=row['source'],relativePath=row['relativePath'],bytes=row['bytes'],mtime_ns=row['mtime_ns'],kind=source_kind(source),scopes=[],categories=[])
        target=chosen[ident]
        for scope in scopes:
            if scope not in target['scopes']:target['scopes'].append(scope)
        if category and category not in target['categories']:target['categories'].append(category)

    # Preserve all known MAGIX deferred rows, with fresh status; do not hydrate or auto-promote.
    frozen_deferred=magix.get('excludedOrDeferred',[])
    for old in frozen_deferred:
        row,reason=inspect_source(old['source'],old)
        add_deferred({**row,'frozenStatus':old.get('status'),'currentMetadataReason':reason},reason or 'Frozen MAGIX deferred entry; not automatically promoted despite current local metadata',['magix_media'], 'frozen_magix_deferred')
    for row in current:
        scopes=['codex_projects']+[f"plan_entry:{e['id']}" for e in entries if capture_export.under(row['source'],e['source'])]
        add_candidate(row['source'],scopes,row,'current_project_enumeration')
    for old in magix['files']:
        row,reason=inspect_source(old['source'],old)
        if reason or (row['bytes'],row['mtime_ns'])!=(old['bytes'],old['mtime_ns']):
            magix_changes.append(dict(source=old['source'],previousBytes=old['bytes'],currentBytes=row['bytes'],previousMtimeNs=old['mtime_ns'],currentMtimeNs=row['mtime_ns'],currentReason=reason,status='NOW_DEFERRED' if reason else 'CURRENT_METADATA_DIFFERS'))
        add_candidate(old['source'],['magix_media'],old,'frozen_magix_ready',old.get('category'))

    for old in current_exclusions:
        entry={**old,'scope':'codex_projects','origin':'current_enumeration'}
        try:entry['relativePath']=mapping(old['source'])
        except RuntimeError:entry['relativePath']=None
        exclusions.append(entry)
        reason=old['reason']
        if reason.startswith('INVENTORY_ERROR') or reason.startswith('Link/reparse') or reason.startswith('Symbolic link'):
            source=old['source'];fallback=dict(source=source,relativePath=entry['relativePath'],bytes=None,mtime_ns=None,attributes=None)
            try:
                st=Path(source).lstat();fallback.update(bytes=None if stat.S_ISDIR(st.st_mode) else st.st_size,mtime_ns=st.st_mtime_ns,attributes=getattr(st,'st_file_attributes',0));is_subtree=stat.S_ISDIR(st.st_mode)
            except OSError:is_subtree=True
            add_deferred(fallback,reason,['codex_projects'],'current_enumeration_blocked_path',is_subtree)

    support=[
        BASE/'outputs'/'Codex-Project-Handoff-2026-10-28.zip',
        BASE/'outputs'/'Codex-Project-Handoff-2026-10-28.sha256.txt',
        MAGIX_ROOT/'MAGIX-Migration-Guide-2026-10-29.zip',
        MAGIX_ROOT/'MIGRATION-NOTES.txt',MAGIX_ROOT/'dependency-report.json',
        MAGIX_ROOT/'unresolved-references.csv',MAGIX_ROOT/'Cloud-deferred-files.csv',
        MAGIX_ROOT/'selection.json',MAGIX_SELECTION,MAGIX_ROOT/'SELECTION-AUDIT.json',
        BASE/'outputs'/'Full-Project-Export-2026-10-28'/'Batch-001'/'batch-001-exclusions.json',
        PLAN,
        BASE/'outputs'/'Magix-Video-Inventory-2026-10-29'/'INVENTORY-SUMMARY.json',
        BASE/'outputs'/'Magix-Video-Inventory-2026-10-29'/'MAGIX-projects-and-backups.csv',
        BASE/'outputs'/'Magix-Video-Inventory-2026-10-29'/'Personal-videos.csv',
        BASE/'outputs'/'Magix-Video-Inventory-2026-10-29'/'MAGIX-settings-findings.json',
    ]
    for path in support:add_candidate(str(path),['handoff_support'],origin='explicit_support_artifact')

    # Compare refreshed original project sources against the prior immutable inventory.
    old_inventory=[];old_state_summary=None
    if OLD_STATE.exists():
        old_state=read_json(OLD_STATE);old_inventory=old_state.get('inventory',[])
        old_state_summary=dict(path=str(OLD_STATE),inventoryFiles=len(old_inventory),createdUtc=old_state.get('createdUtc'))
        del old_state
    old_by_source={canonical(r['source']):r for r in old_inventory}
    new_by_source={canonical(r['source']):r for r in current}
    new_sources=[dict(source=r['source'],bytes=r['bytes'],mtime_ns=r['mtime_ns']) for ident,r in new_by_source.items() if ident not in old_by_source]
    changed_sources=[]
    for ident,new in new_by_source.items():
        old=old_by_source.get(ident)
        if old and (old['bytes'],old['mtime_ns'])!=(new['bytes'],new['mtime_ns']):changed_sources.append(dict(source=new['source'],previousBytes=old['bytes'],currentBytes=new['bytes'],previousMtimeNs=old['mtime_ns'],currentMtimeNs=new['mtime_ns']))
    absent_sources=[]
    for ident,old in old_by_source.items():
        if ident in new_by_source:continue
        now,reason=inspect_source(old['source'],old)
        absent_sources.append(dict(source=old['source'],previousBytes=old['bytes'],currentBytes=now['bytes'],status='CURRENTLY_UNAVAILABLE_OR_DEFERRED' if reason else 'EXISTS_BUT_NOT_IN_CURRENT_PLAN_ENUMERATION',currentReason=reason))
        if reason:
            add_deferred(now,reason,['codex_projects'],'prior_project_inventory_now_unavailable')

    files=sorted(chosen.values(),key=lambda r:r['relativePath'].casefold())
    capture_export.validate_destinations([dict(path=r['relativePath']) for r in files])
    if any(ident in chosen for ident in deferred):raise RuntimeError('Source is both selected and deferred')
    if len({r['relativePath'].casefold() for r in files})!=len(files):raise RuntimeError('Destination collision')
    for row in files:
        if not isinstance(row['bytes'],int) or not isinstance(row['mtime_ns'],int):raise RuntimeError('Invalid current file metadata')
        if row['kind'] not in ('file','sqlite','jsonl'):raise RuntimeError('Invalid file kind')
    deferred_rows=sorted(deferred.values(),key=lambda r:r['source'].casefold())
    scope_counts=collections.Counter(scope for row in files for scope in row['scopes'] if not scope.startswith('plan_entry:'))
    category_counts=collections.Counter(cat for row in files for cat in row['categories'])
    kind_counts=collections.Counter(row['kind'] for row in files)
    summary=dict(files=len(files),bytes=sum(r['bytes'] for r in files),GiB=sum(r['bytes'] for r in files)/1024**3,
                 kindCounts=dict(kind_counts),scopeCounts=dict(scope_counts),magixCategoryCounts=dict(category_counts),
                 scopeCountsMayOverlap=True,deduplicatedRepeatedSourceSelections=duplicate_sources,
                 currentProjectEnumeratedFiles=len(current),currentProjectEnumeratedBytes=sum(r['bytes'] for r in current),
                 frozenMAGIXReadyRequested=len(magix['files']),frozenMAGIXCloudDeferredRows=len(frozen_deferred),
                 deferredEntries=len(deferred_rows),deferredFileEntries=sum(not r['subtree'] for r in deferred_rows),
                 deferredSubtreeEntries=sum(r['subtree'] for r in deferred_rows),deferredKnownFileBytes=sum(r['bytes'] or 0 for r in deferred_rows if not r['subtree']),
                 exclusions=len(exclusions),currentInventoryErrors=sum(r['reason'].startswith('INVENTORY_ERROR') for r in exclusions),
                 newProjectSourcesVsPriorInventory=len(new_sources),changedProjectSourcesVsPriorInventory=len(changed_sources),
                 oldSourcesAbsentFromCurrentEnumeration=len(absent_sources),frozenMAGIXMetadataChanges=len(magix_changes),
                 unresolvedMAGIXReferenceCandidates=2532,unresolvedMAGIXNoLocalCandidate=1800,
                 unresolvedMAGIXSameBasenameIdentityCandidates=732)
    notes=[
        'Metadata-only refreshed selection. No share access, payload copying, source hashes, hydration, or application actions occurred during this build.',
        'Every file maps to its original absolute C: layout beneath Drive_C. The receiving root must be new and must not overwrite a live user profile.',
        'Exact case-insensitive absolute source paths are deduplicated across scopes. Different paths with equal content remain separate; no content identity or hardlink alias deduplication is claimed.',
        'The active TheStats database is marked sqlite for a consistent SQLite backup. Its current WAL/SHM/journal is excluded. Historical databases/test sidecars remain ordinary byte-preserved files.',
        'Codex session and archived-session .jsonl files are marked jsonl for complete-line prefix snapshots. Compressed .jsonl.zst files remain ordinary files.',
        'Twenty-one previously deferred MAGIX cloud files remain explicitly disclosed; no placeholders were hydrated or automatically promoted.',
        'MAGIX source strings do not prove a complete active dependency graph. 732 unresolved identities have unverified same-basename candidates selected; 1800 candidates had no local match. No aliases or project relinking were performed.',
        'Original project/MAGIX selection exclusions and dependency reports are included as support files. Bulk generated export ZIP parts are excluded by the existing task-root exclusion.',
        'The two small prepared handoff/guide ZIPs are explicitly included; the Codex handoff contains preserved Git bundles.',
        'Source files may change after this manifest was built. The transfer helper must validate metadata and capture stability rather than treating this inventory as a verified copy.'
    ]
    manifest=dict(format='pc-transfer-selection-v1',createdUtc=stamp(),files=files,deferred=deferred_rows,exclusions=exclusions,summary=summary,notes=notes,
                  inputReferences=dict(projectPlan=str(PLAN),frozenMAGIXSelection=str(MAGIX_SELECTION),oldCaptureState=str(OLD_STATE)))
    report=dict(format='pc-transfer-manifest-build-report-v1',startedUtc=began,finishedUtc=stamp(),status='METADATA_SELECTION_BUILT',summary=summary,
                supportArtifacts=[str(p) for p in support],exclusionReasonCounts=dict(collections.Counter(r['reason'] for r in exclusions)),
                comparisonToPrevious=dict(oldState=old_state_summary,newSources=new_sources,changedSources=changed_sources,oldSourcesAbsentFromCurrentEnumeration=absent_sources,frozenMAGIXChanges=magix_changes),notes=notes)
    OUT.mkdir(parents=True,exist_ok=True)
    write_json_new(manifest_path,manifest);write_json_new(report_path,report)
    print(json.dumps(dict(status='MANIFEST_FROZEN',manifest=str(manifest_path),report=str(report_path),summary=summary),indent=2),flush=True)

if __name__=='__main__':build()
