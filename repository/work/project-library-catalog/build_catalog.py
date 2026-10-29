"""Group the frozen migration manifest; no source payload reads or publication."""
import collections
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sys

BASE = Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep')
HELPER = BASE/'outputs/Project-Library-2026-10-29/Helper'
WORK = BASE/'work/project-library-catalog'
MANIFEST = BASE/'outputs/Ethernet-Transfer-2026-10-29/Copy-Helper/transfer-manifest.json'
MAGIX = BASE/'outputs/MAGIX-iCloud-Export-2026-10-29'

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def key(path):return ntpath.normpath(str(path)).casefold()
def below(path,parent):return key(path)==key(parent) or key(path).startswith(key(parent).rstrip('\\')+'\\')
def ident(prefix,label,identity):
    slug=re.sub('[^a-z0-9]+','-',label.casefold()).strip('-')[:58] or 'project'
    return prefix+'-'+slug+'-'+hashlib.sha256(key(identity).encode('utf-8')).hexdigest()[:10]
def write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if '--replace-prepared' not in sys.argv:raise RuntimeError('Prepared output already exists: '+str(path))
        temp=path.with_name(path.name+'.new')
        with temp.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,separators=(',',':'))
        os.replace(temp,path)
    else:
        with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,separators=(',',':'))

def main():
    m=read(MANIFEST);selection=read(MAGIX/'selection.json');deps=read(MAGIX/'dependency-report.json')
    rows={key(r['source']):r for r in m['files']};by_path={r['relativePath']:r for r in m['files']}
    assert len(rows)==len(by_path)==m['summary']['files']
    groups={};assigned=set()
    def group(gid,name,description,notes=(),warnings=(),category='project'):
        if gid not in groups:groups[gid]=dict(id=gid,name=name,description=description,category=category,files={},notes=list(notes),warnings=list(warnings))
        return groups[gid]
    def add(g,r):
        g['files'][r['relativePath']]=dict(path=r['relativePath'],sourceRelative='Sources/'+r['relativePath'],bytes=r['bytes'],mtime_ns=r['mtime_ns'],kind='sqlite_snapshot' if r['kind']=='sqlite' else r['kind'])
        assigned.add(key(r['source']))
    active=group('stats-active','The Stats — current app and library','Current application source, documentation and complete AppData library, including registered analyses, collections and archives.',
        ['Fetch into a local disk working folder. Install Python dependencies from the project requirements.',
         'Set STATS_DATA_DIR to the fetched Drive_C/Users/jlewi/AppData/Local/TheStats folder before launching. Do not run SQLite directly over the network.',
         'The active database is published as a consistent SQLite snapshot. Original project data is available separately.'],
        ['Historical analysis and migration utilities may require the separate Stats source-data project; lean selection is not validation of every historical utility.'])
    data=group('stats-source-data','The Stats — original source data','Preserved project data for historical analyses, recovery and import utilities. Fetch when those tasks require it.',category='optional-history')
    hist={x:group('stats-history-'+x,'The Stats — '+x,'Optional preserved '+x+' from the original Stats workspace.',category='optional-history') for x in ('backups','outputs','test-results')}
    mw=group('mw5-companion','MW5 Lancemate Companion','Full selected workspace plus both user-authored native plugin directories.',
        ['Engine/editor installations, generated dependencies and credentials are outside this selection.','Git bundles are available in the separate migration guides project.'])
    context=group('codex-context','Codex — context and tools','Selected skills, memories, rules, plans, automations and project context. Does not include authentication or live internal databases.',warnings=['These are portable files, not a guaranteed automatic import of the desktop application state.'])
    sessions=group('codex-conversation-archive','Codex — conversation archives','Selected session and archived-session history. Active JSONL logs are fetched as complete-line prefix snapshots.',category='optional-history')
    artwork=group('codex-images-visualizations','Codex — images and visualizations','Saved generated images, attachments and visualizations.',category='optional-history')
    support=group('migration-guides','Migration guides and recovery bundles','The two prepared small handoff/guide ZIPs and finalized inventory/dependency reports.',
        ['The Codex handoff ZIP includes preserved Git bundles.','Dependency reports retain unresolved paths and deferred cloud files.'],category='support')
    vdown=group('video-downloader-scripts','Video downloader — scripts','Portable downloader and conversion source files; tool binaries and footage are separate.')
    for r in rows.values():
        src=r['source'];scopes=r['scopes']
        if 'handoff_support' in scopes:add(support,r)
        if 'plan_entry:stats-library' in scopes:add(active,r)
        if 'plan_entry:chatgpt-projects' in scopes:
            tail=src.split('\\ChatGPT\\',1)[1];parts=tail.split('\\')
            if parts[0].startswith('The Stats'):
                top=parts[1] if len(parts)>1 else ''
                add(data if top=='data' else hist[top] if top in hist else active,r)
            elif parts[0]=='MW5 LANCEMATE COMPANION':add(mw,r)
            else:add(group(ident('chatgpt',parts[0],parts[0]),parts[0],'Selected ChatGPT project workspace.'),r)
        if any(s in scopes for s in ('plan_entry:LanceObserverProbe','plan_entry:LanceObserverDiagnosticControl')):add(mw,r)
        if 'plan_entry:codex-workspaces' in scopes:
            tail=src.split('\\Documents\\Codex\\',1)[1];parts=tail.split('\\')
            label='/'.join(parts[:2]) if len(parts)>=3 else ('/'.join(parts[:-1]) or 'Workspace root files')
            add(group(ident('workspace',label,label),'Codex workspace — '+label,'Preserved files for this individual workspace; applications and dependencies may require installation.'),r)
        for scope in scopes:
            if not scope.startswith('plan_entry:'):continue
            eid=scope[11:]
            if eid.startswith('codex-') and eid!='codex-workspaces':
                add(sessions if eid in ('codex-sessions','codex-archived_sessions') else artwork if eid in ('codex-generated_images','codex-visualizations','codex-attachments','codex-codex-remote-attachments') else context,r)
            elif eid in ('ECS_Sales_Assist_v0.1','Synthetic_Sales_Assist_Specification_Bundle_v1.0.1','Synthetic_Sales_Assist_Specification_Bundle','synthetic-sales-assist'):
                add(group(ident('sales',eid,eid),eid,'Separate preserved Sales Assist variant; versions remain distinct.'),r)
            elif eid=='video-downloader-source':add(vdown,r)

    # Recovery families use only mechanical _BAKn stripping within the same directory.
    # Crash folders and manually named variants remain separate unless exact family paths agree.
    magix_rows={key(r['source']):r for r in selection['files']}
    families=collections.defaultdict(list)
    for r in selection['files']:
        if r['category'] not in ('magix_project','magix_project_backup'):continue
        p=Path(r['source']);stem=re.sub(r'(?:_BAK\d+)+$','',p.stem,flags=re.I)
        families[(str(p.parent).casefold(),stem.casefold())].append(r)
    refs_by_project=collections.defaultdict(set);refs={r['id']:r for r in deps['references']}
    for ref in deps['references']:
        for obs in ref['observed_in']:refs_by_project[key(obs['project'])].add(ref['id'])
    family_report=[]
    for family,inputs in sorted(families.items()):
        main=next((r for r in inputs if r['category']=='magix_project'),inputs[0]);name=re.sub(r'(?:_BAK\d+)+$','',Path(main['source']).stem,flags=re.I)
        gid=ident('magix',name,'|'.join(family));refids=set().union(*(refs_by_project[key(r['source'])] for r in inputs))
        linked=[r for r in selection['files'] if set(r['reference_ids'])&refids]
        folders=set().union(*(set(r['coverage_folders']) for r in linked)) if linked else set()
        for folder in deps['focusedFolders']:
            if any(below(r['source'],folder) for r in inputs):folders.add(folder)
        coverage=[r for r in selection['files'] if set(r['coverage_folders'])&folders]
        chosen={key(r['source']):r for r in inputs+linked+coverage}
        unresolved=[rid for rid in refids if refs[rid]['resolution']!='EXISTING_PATH_CANDIDATE_SELECTED']
        missing=[rid for rid in unresolved if refs[rid]['resolution']=='UNRESOLVED_NO_LOCAL_CANDIDATE']
        candidates=[rid for rid in unresolved if refs[rid]['resolution']=='UNRESOLVED_IDENTITY_SAME_BASENAME_CANDIDATES_SELECTED']
        deferred=[r for k,r in chosen.items() if k not in rows]
        g=group(gid,'MAGIX — '+name,'Project/recovery family plus existing path candidates, same-basename alternatives and related focused-folder assets.',
            ['Preserves all selected family variants and original paths; no project relinking or aliases were created.',
             'The full dependency report is in Migration guides. Application/codec installation and manual relinking can still be required.'],
            ['Dependency extraction is heuristic, not a complete active timeline dependency graph.',
             f'{len(unresolved)} unresolved reference candidates: {len(missing)} with no local match and {len(candidates)} with unverified basename alternatives.',
             f'{len(deferred)} related files remain deferred. Focused-folder assets may exceed the minimum required.'],category='magix-project')
        for k,r in chosen.items():
            if k in rows:add(g,rows[k])
        g['dependencyProvenance']=dict(projectSources=[r['source'] for r in inputs],referenceIds=sorted(refids),unresolvedReferenceIds=sorted(unresolved),focusedFolders=sorted(folders),deferredSources=[r['source'] for r in deferred],method=deps['method'],completeDependencyGraph=False)
        family_report.append(dict(id=gid,name=g['name'],projectVariants=len(inputs),selectedFiles=len(g['files']),bytes=sum(r['bytes'] for r in g['files'].values()),references=len(refids),unresolved=len(unresolved),noLocalMatch=len(missing),candidateIdentities=len(candidates),deferredFiles=len(deferred)))

    # Folder archives keep optional footage available without forcing it into every project.
    for r in selection['files']:
        k=key(r['source']);folder=str(Path(r['source']).parent)
        if r['category']=='personal_video':
            g=group(ident('videos',Path(folder).name,folder),'Videos — '+folder,'Optional personal video files in this folder, including preserved proxy variants.',category='video-archive')
            if k in rows:add(g,rows[k])
            else:g.setdefault('deferredSources',[]).append(r['source'])
    for k,r in magix_rows.items():
        if k in rows and k not in assigned:
            folder=str(Path(r['source']).parent)
            add(group(ident('media',Path(folder).name,folder),'Media assets — '+folder,'Selected supporting audio, images and sidecars not otherwise assigned to a project family.',category='optional-media'),rows[k])
    leftovers=[r for k,r in rows.items() if k not in assigned]
    if leftovers:
        g=group('other-selected-files','Other selected files','Frozen-selection files without a more specific project group.',category='support')
        for r in leftovers:add(g,r)
    projects=[]
    for g in groups.values():
        if not g['files'] and not g.get('deferredSources'):continue
        g['files']=sorted(g['files'].values(),key=lambda r:r['path'].casefold());g['summary']=dict(files=len(g['files']),bytes=sum(r['bytes'] for r in g['files']))
        paths=[r['path'] for r in g['files']]
        if g['id']=='stats-active':
            code_root=next(r['path'].split('/stats_app/')[0] for r in g['files'] if '/stats_app/' in r['path'])
            data_root='Drive_C/Users/jlewi/AppData/Local/TheStats'
            g['workspaceRelative']='Files/'+code_root;g['workspacePaths']=['Files/'+code_root,'Files/'+data_root]
            g['environmentHints']={'STATS_DATA_DIR':{'relativeToWorkingSet':'Files/'+data_root,'purpose':'Set this environment variable before launching so writes stay in the fetched working set.'}}
            g['entryPoints']=[{'path':'Files/'+code_root+'/launch_windows.bat','type':'script','action':'Review setup instructions, install dependencies locally, then launch with STATS_DATA_DIR set.'}]
        elif g['id']=='mw5-companion':
            roots=[]
            for p in paths:
                if '/ChatGPT/MW5 LANCEMATE COMPANION/' in p:roots.append(p.split('/MW5 LANCEMATE COMPANION/')[0]+'/MW5 LANCEMATE COMPANION')
                elif '/Plugins/' in p:roots.append(p.split('/Plugins/')[0]+'/Plugins/'+p.split('/Plugins/')[1].split('/')[0])
            roots=list(dict.fromkeys(roots));roots.sort(key=lambda p:'/ChatGPT/' not in p)
            g['workspaceRelative']='Files/'+roots[0];g['workspacePaths']=['Files/'+p for p in roots]
            g['warnings'].append('The MW5 editor and plugins need installation/configuration on the new PC; files alone do not install or validate the editor. Keep cache copies intact until any external editor working copies are separately archived.')
        elif g['category']=='magix-project':
            inputs=g['dependencyProvenance']['projectSources'];entry_paths=[rows[key(p)]['relativePath'] for p in inputs if key(p) in rows]
            root_paths=list(dict.fromkeys(p.rsplit('/',1)[0] for p in entry_paths))
            g['workspaceRelative']='Files/'+root_paths[0];g['workspacePaths']=['Files/'+p for p in root_paths]
            g['entryPoints']=[{'path':'Files/'+p,'type':'magix-project' if p.lower().endswith('.mvp') else 'magix-recovery','action':'Open a working copy in MAGIX; resolve missing-media prompts to fetched files inside this working set.'} for p in entry_paths]
        elif paths:
            common=ntpath.commonpath([p.replace('/','\\') for p in paths]).replace('\\','/')
            if common in paths:common=common.rsplit('/',1)[0]
            g['workspaceRelative']='Files/'+common;g['workspacePaths']=[g['workspaceRelative']]
        else:g['workspaceRelative']='Files';g['workspacePaths']=['Files']
        projects.append(g)
    projects.sort(key=lambda g:(g['category'],g['name'].casefold()))
    assert assigned==set(rows)
    assert len({g['id'] for g in projects})==len(projects)
    source_rows=[dict(source=r['source'],path=r['relativePath'],bytes=r['bytes'],mtime_ns=r['mtime_ns'],kind=r['kind']) for r in m['files']]
    created=datetime.now(timezone.utc).isoformat()
    catalog=dict(format='project-library-catalog-v1',catalogId='msi-project-library-v1',createdUtc=created,publicationStatus='SOURCE_CATALOG_PREPARED',projects=projects,publishingSources=source_rows,
        deferred=m['deferred'],exclusions=m['exclusions'],summary=dict(projects=len(projects),uniqueSourceFiles=len(source_rows),uniqueSourceBytes=sum(r['bytes'] for r in source_rows),projectCategories=dict(collections.Counter(g['category'] for g in projects))),
        provenance=dict(manifest=str(MANIFEST),manifestSha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),frozenSelectionCreatedUtc=m['createdUtc'],dependencyReport=str(MAGIX/'dependency-report.json')),
        notes=['File references can occur in multiple projects; uniqueSourceBytes counts each source once.','Published ordinary files are hardlinks to mutable original files, not immutable backups. Never change their ACLs or edit them through the library.','Active Stats SQLite is a separate consistent snapshot. JSONL sources require complete-line prefix capture on the client.','Twenty-one cloud files remain deferred; MAGIX dependencies are incomplete and unverified identities are labeled.','Only the selected sources are published. New files created after the frozen selection require an explicit catalog update.'])
    target=HELPER/'catalog-source.json';write_new(target,catalog)
    report=dict(createdUtc=created,catalog=str(target),catalogBytes=target.stat().st_size,summary=catalog['summary'],projects=[dict(id=g['id'],name=g['name'],category=g['category'],**g['summary']) for g in projects],magixFamilies=family_report,unassignedFallbackFiles=len(leftovers),sourceFilesMetadataOnly=True,notPublished=True)
    write_new(WORK/'CATALOG-BUILD-REPORT.json',report)
    print(json.dumps(dict(status='CATALOG_PREPARED',path=str(target),catalogBytes=target.stat().st_size,summary=catalog['summary'],statsActive=active['summary'],magixFamilies=len(family_report)),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
