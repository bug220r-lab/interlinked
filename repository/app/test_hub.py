"""Isolated hub adapter tests. Synthetic metadata and read-only fake helpers only."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import copy
import importlib.util
import json
import os
import sys
import threading
import time
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hub_under_test',HERE/'interlinked_hub.py')
h=importlib.util.module_from_spec(spec);sys.modules[spec.name]=h;spec.loader.exec_module(h)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/('hub-fixture-'+uuid.uuid4().hex);root.mkdir(parents=True)
    checks=[]
    def check(value,label):
        assert value,label
        checks.append(label)
    def reject(fn,label):
        try:fn()
        except (ValueError,OSError):checks.append(label)
        else:raise AssertionError(label)
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value)+'\n',encoding='utf-8')
    def until(fn):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if fn():return
            time.sleep(.01)
        raise AssertionError('Fixture condition timed out')

    kit=root/'kit';cache=root/'cache';state=root/'state'
    for p in (kit,cache,state):p.mkdir()
    runtime=SimpleNamespace(kit=kit,cache=cache,state=state,python=Path(sys.executable),demo=False,index=dict(projects=[dict(id='demo-magix-1')]))
    catalog=dict(catalogId=h.CATALOG,projects=[
        dict(id='Coding_Project',name='Python coding project',description='Synthetic data workspace',bytes=14,files=2,workspaceRelative='Files/code',warnings=[],notes=[]),
        dict(id='mw5-companion',name='Synthetic MW5 project',bytes=33,files=2,workspaceRelative='Files/plugins',warnings=[],notes=[]),
        dict(id='generic-magix',name='MAGIX collection',bytes=99,files=1,warnings=[],notes=[],entryPoints=[dict(path='Files/example.MVP',type='magix-project',action='Review manually')]),
        dict(id='photos-collection',name='Photographs',bytes=66,files=3,warnings=[],notes=[]),
    ])
    started=threading.Event();gate=threading.Event();calls=[]
    def runner(kind):
        calls.append(kind)
        if kind=='catalog':started.set();gate.wait(5);return copy.deepcopy(catalog)
        return dict(returnsReadable=True,localFreeBytes=123,msiFreeBytes=456)
    before=time.monotonic();hub=h.ProjectHub(runtime,runner=runner)
    check(time.monotonic()-before<1,'Startup never waits for remote catalog discovery')
    check(started.wait(1) and hub.status()['refreshing'],'Discovery runs in a background worker')
    check(hub.rows()==[],'First refresh can display no discovered general rows without blocking MAGIX')
    gate.set();until(lambda:not hub.status()['refreshing'])
    rows=hub.rows();by_original={r['originalId']:r for r in rows};code=by_original['Coding_Project']['id'];mw5=by_original['mw5-companion']['id']
    check(len(rows)==4 and len({r['id'] for r in rows})==4,'Separate library rows receive unique opaque UI identities')
    check(code.startswith('library-') and 'Coding_Project' not in code,'Original mixed-case/underscore ID stays out of UI routing')
    check(by_original['mw5-companion']['type']=='mw5' and by_original['Coding_Project']['type']=='coding_data','MW5 and coding/data categories are derived separately')
    check(by_original['generic-magix']['type']=='magix' and by_original['photos-collection']['type']=='other','Catalog MAGIX collections and unknown types retain distinct capabilities')
    check(hub.status()['libraryStatus']=='AVAILABLE' and hub.status()['returnsStatus']=='AVAILABLE_READ_CHECK_ONLY','Health reports evidence scope instead of write/verification guarantees')
    check(hub.status()['storage']['localFreeBytes']==123 and hub.status()['storage']['msiFreeBytes']==456,'Storage observations are exposed with probe scope')
    check(rows[0]['cache']['status']=='ABSENT' and rows[0]['capabilities']['fetch'],'Unfetched rows permit guarded fetch without pretending local readiness')
    check(not by_original['generic-magix']['capabilities']['openApplication'],'Generic MAGIX catalog does not bypass the reviewed MAGIX launch adapter')
    overlap=copy.deepcopy(catalog);overlap['projects'].append(dict(id='demo-magix-1',name='Already reviewed family',bytes=14,files=2,warnings=[],notes=[]))
    check(len(hub._catalog_projects(overlap))==4,'Exact reviewed MAGIX IDs are omitted from generic adapter to preserve one cache and one workflow')
    overlap['projects'][-1]['id']='DEMO-MAGIX-1'
    reject(lambda:hub._catalog_projects(overlap),'Case-aliased reviewed MAGIX ID is refused instead of creating another route')
    check('demo-magix-1' not in [r['originalId'] for r in hub.rows()],'Reviewed MAGIX family cannot receive generic cache commands')
    command=hub.arguments('fetch',code)
    check(command[-2:]==['fetch','Coding_Project'] and command[:5]==[str(runtime.python),'-I','-B','-X','utf8'],'Library command preserves exact original ID and isolated fixed runtime arguments')
    reject(lambda:hub.arguments('release',code,code),'Opaque routing ID cannot replace exact original project release confirmation')
    check(hub.arguments('release',code,'Coding_Project')[-4:]==['release','Coding_Project','--confirm','Coding_Project'],'Release requires the displayed exact original client ID')
    reject(lambda:hub.arguments('open-arbitrary',code),'Unknown library operation cannot execute a command')
    reject(lambda:hub.arguments('fetch','library-not-real'),'Guessed library UI ID cannot choose a project')
    reject(lambda:hub.folder_path(code),'Absent cache cannot open a working folder')

    # Exercise the actual reviewed client's metadata schema without touching SMB.
    engine_spec=importlib.util.spec_from_file_location('hub_identity_engine',HERE.parent/'outputs/Project-Library-2026-10-29/Client/project_library.py')
    engine=importlib.util.module_from_spec(engine_spec);sys.modules[engine_spec.name]=engine;engine_spec.loader.exec_module(engine)
    client=engine.Client(SimpleNamespace(library=h.LIBRARY,returns=h.RETURNS,cache=str(cache),test_local=False))
    client.catalog=dict(catalogId=h.CATALOG)
    identity=client.identity('Coding_Project')
    check(os.name=='nt' and identity['library']==h.LIBRARY+'\\' and identity['returns']==h.RETURNS+'\\',
          'Native Windows client serializes canonical UNC share roots with trailing separators')
    owner=dict(format='project-library-owned-v1',**identity)
    project=cache/'Coding_Project';(project/'Files/code').mkdir(parents=True)
    write(project/'OWNER.json',owner)
    head=dict(format='project-library-head-v1',catalogId=h.CATALOG,projectId='Coding_Project',versionId='a'*32,manifestSha256='b'*64)
    metadata=dict(format='project-library-cache-v1',**identity,status='READY',savedHead=head,baselineHead=head,savedUnixTime=time.time()-10)
    write(project/'state.json',metadata)
    with (project/'events.jsonl').open('w',encoding='utf-8') as stream:
        stream.write(json.dumps(dict(event='VERIFY_PASSED',time=time.time()-5,head=head))+'\n')
    hub.invalidate();ready={r['id']:r for r in hub.rows()}[code]
    check(ready['cache']['status']=='READY' and ready['capabilities']['save'] and ready['capabilities']['verify'],'Owned READY metadata enables reviewed save/verify operations')
    check(hub.folder_path(code)==project/'Files/code','Folder open is confined to catalog workspace inside owned cache Files')
    check(ready['lastSaved']['versionId']=='a'*32 and ready['lastVerified']['currentBaseline'],'Last saved and verification receipts include matching version evidence')
    check('not been rechecked' in ready['lastVerified']['scope'] and 'Metadata only' in ready['contentVerification'],'Historical verification never claims current file content was checked')
    metadata['baselineHead']=dict(head,versionId='c'*32);write(project/'state.json',metadata);hub.invalidate()
    check(not {r['id']:r for r in hub.rows()}[code]['lastVerified']['currentBaseline'],'Verification for an earlier baseline is visibly stale')
    metadata['pendingSave']=dict(head=head);write(project/'state.json',metadata);hub.invalidate()
    check({r['id']:r for r in hub.rows()}[code]['cache']['status']=='PENDING_SAVE','Pending save is distinguished from READY')
    reject(lambda:hub.folder_path(code),'Pending save prevents a fresh external editing session')
    metadata.pop('pendingSave');write(project/'state.json',metadata)
    write(project/'OWNER.json',dict(owner,library=h.LIBRARY))
    reject(lambda:hub.cache_state(code),'Noncanonical ownership spelling is not silently accepted')
    write(project/'OWNER.json',owner)
    write(project/'state.json',dict(metadata,returns=h.RETURNS))
    reject(lambda:hub.cache_state(code),'Noncanonical cache-state spelling is not silently accepted')
    write(project/'state.json',metadata)
    write(project/'OWNER.json',dict(owner,returns=r'\\untrusted\Returns'));hub.invalidate()
    check({r['id']:r for r in hub.rows()}[code]['cache']['status']=='CHECK_REQUIRED','Wrong share ownership blocks local readiness')
    reject(lambda:hub.folder_path(code),'Wrong ownership cannot open external folder')
    write(project/'OWNER.json',owner);hub.invalidate()

    hub.set_favorite(code,True);hub.set_favorite('demo-magix-1',True);hub.mark_recent(code);hub.mark_recent(code)
    check(hub.preferences()['favorites']==[code,'demo-magix-1'] and len(hub.preferences()['recent'])==1,'Favorites support both adapters and recents deduplicate by opaque identity')
    hub.set_favorite(code,False)
    check(hub.preferences()['favorites']==['demo-magix-1'],'Favorite removal is explicit and durable')
    reject(lambda:hub.set_favorite(code,'yes'),'Favorite toggle requires a Boolean')
    reject(lambda:hub.mark_recent('arbitrary-id'),'Preferences cannot introduce an arbitrary project')
    check((state/'hub/preferences.json').exists() and not (project/'Files/preferences.json').exists(),'Preferences stay outside managed project content')

    hub.close()
    reject(lambda:hub.mark_recent(code),'Closing hub rejects preference writes')
    def offline(kind):raise OSError('Synthetic MSI offline')
    old=h.ProjectHub(runtime,runner=offline,auto_refresh=False)
    check(len(old.rows())==4 and old.status()['catalogStale'],'Offline startup retains validated cached catalog with stale label')
    check(old.preferences()['favorites']==['demo-magix-1'],'Private preferences survive app restart')
    old.refresh();until(lambda:not old.status()['refreshing'])
    offline_code={r['id']:r for r in old.rows()}[code]
    check(old.status()['libraryStatus']=='UNAVAILABLE' and len(old.rows())==4,'Offline refresh preserves prior catalog and reports unavailability')
    check(not offline_code['capabilities']['save'] and not offline_code['capabilities']['fetch'],'Offline rows cannot present remote mutation as ready')
    check(offline_code['capabilities']['openFolder'] and old.folder_path(code)==project/'Files/code','Offline owned local folder remains available independently of remote health')
    check('localFreeBytes' in old.status()['storage'],'Local capacity remains visible while MSI is offline')
    old.close()

    # Recovery metadata is independent of catalogs, managed caches and opening.
    preference_state=root/'preference-state';preference_state.mkdir()
    preference_runtime=SimpleNamespace(**dict(vars(runtime),state=preference_state))
    preference_hub=h.ProjectHub(preference_runtime,runner=offline,auto_refresh=False)
    legacy=dict(format='interlinked-hub-preferences-v1',favorites=['dormant-project'],recent=[dict(id='dormant-project',utc='2025-01-02T03:04:05+00:00')])
    write(preference_hub.prefs_path,legacy)
    raw=preference_hub.prefs_path.read_bytes()
    check(preference_hub.preferences()['preferenceRevision'].startswith('legacy:') and preference_hub.prefs_path.read_bytes()==raw,'Legacy preference reads preserve bytes and expose a stable revision')
    preference_hub.set_favorite('demo-magix-1',True)
    snapshot=preference_hub.history()['items'][0]['id']
    stale=preference_hub.preview_restore(dict(snapshotId=snapshot))
    preference_hub.mark_recent('demo-magix-1')
    reject(lambda:preference_hub.restore(dict(previewId=stale['previewId'])),'Preference restore refuses an intervening delta')
    preview=preference_hub.preview_restore(dict(snapshotId=snapshot))
    restored=preference_hub.restore(dict(previewId=preview['previewId']))
    check(restored['favorites']==legacy['favorites'] and restored['recent']==legacy['recent'],'Restoration preserves dormant IDs and exact original recent UTC values')
    check(restored['preferencesAvailable'] and not list(preference_state.rglob('Files')),'Preference restoration does not fetch or open project files')
    preference_hub.set_favorite('demo-magix-1',True)
    recovery_snapshot=preference_hub.history()['items'][0]['id']
    preference_hub.prefs_path.write_bytes(b'{broken synthetic preferences')
    preference_hub.close()
    corrupt=h.ProjectHub(preference_runtime,runner=offline,auto_refresh=False)
    check(not corrupt.preferences()['preferencesAvailable'] and corrupt.status()['libraryStatus']=='NOT_CHECKED','Corrupt preference content stays unavailable without stopping library startup')
    reject(lambda:corrupt.set_favorite('demo-magix-1',False),'Normal preference writes cannot overwrite corrupted content')
    recovered=corrupt.restore(dict(previewId=corrupt.preview_restore(dict(snapshotId=recovery_snapshot))['previewId']))
    check(recovered['preferencesAvailable'] and len(list(corrupt.config.history_root.glob('corrupt-*.bin')))==1,'Explicit preference recovery preserves corrupt source bytes')
    corrupt.close()

    bad=copy.deepcopy(catalog);bad['projects'].append(dict(bad['projects'][0],id='coding_project'))
    reject(lambda:h.validate_catalog(bad),'Case-colliding client IDs are rejected')
    for value in ('../escape','CON','x/escape','x:escape','x;whoami'):
        reject(lambda value=value:h.key_for(value),'Unsafe original ID rejected: '+value)
    for value in ('../outside','Files/../outside','C:/Windows','Files/CON','Files/link:stream','Files/trailing.','Files//duplicate'):
        bad=copy.deepcopy(catalog);bad['projects'][0]['workspaceRelative']=value
        reject(lambda bad=bad:h.validate_catalog(bad),'Unsafe workspace rejected: '+value)
    bad=copy.deepcopy(catalog);bad['catalogId']='another-library'
    reject(lambda:h.validate_catalog(bad),'Foreign catalog identity cannot enter this installation')
    bad=copy.deepcopy(catalog);bad['projects'][0]['bytes']=-1
    reject(lambda:h.validate_catalog(bad),'Negative catalog size rejected')
    bad=copy.deepcopy(catalog);bad['projects'][0]['files']=True
    reject(lambda:h.validate_catalog(bad),'Boolean does not pass as file count')
    bad=copy.deepcopy(catalog);bad['projects'][0]['entryPoints']=[dict(path='Files/a.cmd',type='script',action='review',command='evil')]
    reject(lambda:h.validate_catalog(bad),'Catalog entry cannot inject a command field')
    bad=copy.deepcopy(catalog);bad['projects'][0]['environmentHints']=dict(STATS_DATA_DIR=dict(relativeToWorkingSet='../outside'))
    reject(lambda:h.validate_catalog(bad),'Environment hints cannot point outside managed Files')
    bad=copy.deepcopy(catalog);bad['projects'][0]['workspacePaths']='Files/code'
    reject(lambda:h.validate_catalog(bad),'Workspace list must be an actual bounded list')
    reject(lambda:old._validate_preferences(dict(format='interlinked-hub-preferences-v1',favorites=[{}],recent=[])),'Malformed preference values fail closed')
    reject(lambda:h.ProjectHub(SimpleNamespace(**dict(vars(runtime),kit=root/'foreign-kit')),runner=offline,auto_refresh=False),'Hub owner pins prevent foreign kit state reuse')

    # Fake read-only engine returns oversized metadata, never accesses real shares.
    client=kit/'Project-Library-Client';client.mkdir()
    (client/'project_library.py').write_text("import sys\nsys.stdout.write('x'*(17*1024*1024))\n",encoding='utf-8')
    readonly=h.ProjectHub(runtime,runner=offline,auto_refresh=False)
    reject(lambda:readonly._run('catalog'),'Read-only child output is bounded before JSON is trusted')
    readonly.close()
    check(calls==['catalog','probe'],'Initial discovery uses only fixed catalog and read-only probe commands')
    check(not list(root.rglob('*.MVP')) and not list(root.rglob('*.uasset')),'Hub tests never create proprietary project assets or launch an editor')

    result=dict(status='PASS',count=len(checks),checks=checks,scope='Synthetic owned local metadata and read-only fake subprocess only; no shares or applications')
    receipt=root/'hub-test-results.json';receipt.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',count=len(checks),receipt=str(receipt))))


if __name__=='__main__':main()
