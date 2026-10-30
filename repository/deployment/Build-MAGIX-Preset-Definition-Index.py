"""Read saved inventories; hash only small preset/content definitions on MSI.

Writes new private JSON reports, never copies payload or changes installed files.
No remote access is performed. The resulting index is verification-only.
"""
from __future__ import annotations
import argparse,collections,ctypes,datetime,hashlib,json,os,pathlib,re,stat,sys,uuid

MEDIA={'.bmp','.jpg','.jpeg','.png','.ogg','.mp4','.mxv','.wmv','.wav','.tga','.tif','.tiff','.avi','.mp3','.flac'}
DERIVED={'.hdp','.h0','.log'}
RUNTIME={'.exe'}
BUNDLES={'.mis'}
MAX_FILE=32*1024*1024
MAX_TOTAL=250*1024*1024
KINDS={'steam_content':'steam_content','roaming_edition':'roaming_preset'}
def need(test,message):
    if not test:raise ValueError(message)
def read(path):return json.loads(path.read_text('utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fs(path):return '\\\\?\\'+str(path) if os.name=='nt' and not str(path).startswith('\\\\') else str(path)
def relative(value):
    need(isinstance(value,str),'Relative path must be text.')
    parts=value.replace('\\','/').split('/')
    need(parts and all(p and p not in ('.','..') and not any(c in p for c in ':\x00\r\n') and not p.endswith((' ','.')) for p in parts),'Unsafe inventory path.')
    return '/'.join(parts)
def ordinary(path):
    need(path.is_absolute() and not str(path).startswith('\\\\'),'Local absolute source path required.')
    for part in reversed((path,*path.parents)):
        s=os.lstat(fs(part));need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&(0x400|0x1000|0x40000|0x400000),'Link/offline/recall source refused.')
    return s
def classification(row):
    root=row['root'];rel=relative(row['relativePath']);parts=rel.split('/');ext=pathlib.PurePosixPath(rel).suffix.lower()
    if root not in KINDS:return 'OUTSIDE_PRESET_ROOTS'
    if root=='roaming_edition':return 'DEFINITION' if parts[0].casefold()=='presets' else 'APP_STATE_OR_PROJECT_NOT_PRESET'
    if ext in MEDIA:return 'MEDIA_OR_THUMBNAIL_METADATA_ONLY'
    if ext in DERIVED:return 'DERIVED_CACHE_OR_LOG_METADATA_ONLY'
    if ext in RUNTIME:return 'RUNTIME_NOT_PRESET'
    if ext in BUNDLES:return 'MOVIE_TEMPLATE_BUNDLE_METADATA_ONLY'
    if row['bytes']>MAX_FILE:return 'OVERSIZED_DEFINITION_METADATA_ONLY'
    return 'DEFINITION'
def category(row):
    rel=relative(row['relativePath']);parts=rel.split('/');top=parts[0]
    if row['root']=='roaming_edition':return 'Export presets'
    if top=='UserVideoEffects':return 'User video effects'
    if top=='Titles':return 'Titles and title templates'
    if top=='AudioEffects':return 'Audio effects'
    if 'transition' in rel.casefold():return 'Transitions'
    if top=='MovieTemplates':return 'Movie template definitions'
    if top in ('IntroOutro','OverlayFX','VideoEffects','FX-Preset','Plugins','DVD','MusicEditor','Soundtrack Maker Styles'):return top
    return 'Content support / '+top
def custom(row,missing):
    if not missing:return None
    rel=relative(row['relativePath']);ext=pathlib.PurePosixPath(rel).suffix.lower()
    if row['root']=='roaming_edition' and rel.startswith('Presets/') and ext=='.pru':return 'Custom export preset'
    if row['root']=='steam_content':
        if rel.startswith('UserVideoEffects/') and ext in ('.ifx','.moc'):return 'Custom video effect'
        if rel.startswith('Titles/') and ext=='.tfx':return 'Custom title'
        if rel.startswith('Plugins/TransitionPresets/') and ext=='.mxt':return 'Custom transition'
    return None
def stable_hash(path,expected_size):
    before=ordinary(path);need(stat.S_ISREG(before.st_mode) and before.st_size==expected_size,'Source no longer matches inventory size.')
    if os.name=='nt':
        import msvcrt
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];k.CreateFileW.restype=ctypes.c_void_p
        h=k.CreateFileW(fs(path),0x80000000,1,None,3,0x00300000,None)
        if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
        except BaseException:k.CloseHandle.argtypes=[ctypes.c_void_p];k.CloseHandle(h);raise
        stream=os.fdopen(fd,'rb')
    else:stream=open(path,'rb')
    with stream:
        opened=os.fstat(stream.fileno());need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns),'Source changed before read lock.')
        digest=hashlib.sha256();count=0
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block);count+=len(block)
        after=os.fstat(stream.fileno());need((opened.st_size,opened.st_mtime_ns)==(after.st_size,after.st_mtime_ns) and count==expected_size,'Source changed during hashing.')
        return digest.hexdigest(),opened.st_mtime_ns
def save(path,value):
    need(not path.exists(),'Output already exists: '+path.name)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8',newline='\n') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.rename(temporary,path)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-inventory',type=pathlib.Path,required=True);parser.add_argument('--new-pc-inventory',type=pathlib.Path,required=True);parser.add_argument('--output',type=pathlib.Path,required=True)
    args=parser.parse_args();ordinary(args.output)
    source=read(args.source_inventory);dest=read(args.new_pc_inventory)
    need(source.get('format')==dest.get('format')=='interlinked-magix-preset-font-inventory-v1' and source.get('computer')=='MSI' and dest.get('computer')=='THIS-MACHINE','Expected MSI/new-PC inventories.')
    need(source.get('version')==dest.get('version')=='20,0,1,80','MAGIX inventory versions differ.')
    roots={r['id']:pathlib.Path(r['path']) for r in source['roots'] if r.get('status')=='enumerated'}
    destination={};seen=set()
    for row in dest['files']:
        key=(row['root'],relative(row['relativePath']).casefold());need(key not in destination,'Duplicate destination inventory path.');destination[key]=row
    selection=[r for r in source['files'] if classification(r)=='DEFINITION']
    need(len(selection)<=10000 and sum(r['bytes'] for r in selection)<=MAX_TOTAL,'Definition verification exceeds bounded size/count.')
    files=[];comparisons=[];missing=[];omissions=[];errors=[]
    for n,row in enumerate(source['files']):
        rel=relative(row['relativePath']);key=(row['root'],rel.casefold());need(key not in seen,'Duplicate source inventory path.');seen.add(key)
        target=destination.get(key);meta='MISSING' if target is None else 'SAME_SIZE_UNHASHED' if target['bytes']==row['bytes'] else 'SIZE_DIFFERENT'
        policy=classification(row)
        comparison=dict(root=row['root'],targetRelativePath=rel,bytes=row['bytes'],newPcMetadataStatus=meta,policy=policy)
        if policy!='DEFINITION':omissions.append(comparison);continue
        try:
            path=roots[row['root']].joinpath(*rel.split('/'));digest,mtime=stable_hash(path,row['bytes'])
            item=dict(kind=KINDS[row['root']],targetRelativePath=rel,bytes=row['bytes'],sha256=digest,category=category(row))
            files.append(item);comparison.update(sha256=digest,sourceMtimeNs=mtime);comparisons.append(comparison)
            custom_type=custom(row,target is None)
            if custom_type:missing.append(dict(**item,source=str(path),customType=custom_type))
        except (OSError,ValueError) as exc:errors.append(dict(**comparison,error=str(exc)))
        if len(files)%500==0 and files:print(json.dumps(dict(hashedDefinitions=len(files))),flush=True)
    summary=dict(count=len(files),totalBytes=sum(f['bytes'] for f in files),maxFileBytes=max((f['bytes'] for f in files),default=0),customMissingCount=len(missing),customMissingBytes=sum(f['bytes'] for f in missing),categories=dict(collections.Counter(f['category'] for f in files)),extensions=dict(collections.Counter(pathlib.PurePosixPath(f['targetRelativePath']).suffix.lower() for f in files)),newPcMetadata=dict(collections.Counter(c['newPcMetadataStatus'] for c in comparisons)),omissionPolicies=dict(collections.Counter(r['policy'] for r in omissions)),errors=len(errors))
    provenance=dict(sourceInventorySha256=sha(args.source_inventory),newPcInventorySha256=sha(args.new_pc_inventory),sourceInventoryUtc=source['createdUtc'],newPcInventoryUtc=dest['createdUtc'])
    index=dict(format='interlinked-magix-preset-definition-index-v1',expectedAppVersion='20.0.1.80',createdUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),verificationOnly=True,status='SOURCE_HASHES_VERIFIED' if not errors else 'PARTIAL_ERRORS',provenance=provenance,summary=summary,limitations=['New-PC definition contents have not yet been hashed. Same-size is metadata evidence only.','Media/thumbnail payloads and all 75 MIS movie-template bundles remain metadata-only; this index does not prove all installed assets identical.','No installation, preset payload copying, source edits or remote access occurred.'],files=files)
    save(args.output/'verification-definitions.json',index)
    save(args.output/'missing-custom-presets.json',dict(format='interlinked-magix-missing-custom-presets-v1',provenance=provenance,summary=summary,files=missing))
    save(args.output/'definition-comparison.json',dict(format='interlinked-magix-definition-comparison-v1',provenance=provenance,summary=summary,definitions=comparisons,metadataOnly=omissions,errors=errors))
    print(json.dumps(dict(status=index['status'],summary=summary,indexSha256=sha(args.output/'verification-definitions.json')),indent=2))
    return 1 if errors else 0
if __name__=='__main__':raise SystemExit(main())
