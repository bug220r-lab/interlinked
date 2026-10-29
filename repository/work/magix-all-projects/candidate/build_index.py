"""Rebuild the reviewed offline index from the exact published metadata only."""
import argparse
import hashlib
import json
from pathlib import Path

CATALOG_SHA='9b19a4ef3ba2fb68c06252ef0867db75d2c740d7b55e870f92a2a975271c3464'
ROOTS=('OneDrive/Documents','OneDrive/Pictures','OneDrive/Desktop','Downloads',
 'OneDrive - The University of Akron/The Rialto Theatre','AppData/Roaming/MAGIX/Videodeluxe27_plus_steam')
PREFIX='Drive_C/Users/jlewi/'

def build(source):
    data=Path(source).read_bytes()
    assert hashlib.sha256(data).hexdigest()==CATALOG_SHA,'Published catalog SHA differs; new inventory review required.'
    cat=json.loads(data)
    assert cat['format']=='project-library-catalog-v1' and cat['catalogId']=='msi-project-library-v1'
    projects=[]
    for p in cat['projects']:
        if p.get('category')!='magix-project':continue
        paths={f['path'].casefold():f for f in p['files']}
        roots=[r for r in ROOTS if any(f['path'].casefold().startswith((PREFIX+r+'/').casefold()) for f in p['files'])]
        entries=[]
        for ep in p['entryPoints']:
            assert ep['path'].startswith('Files/')
            rel=ep['path'][6:]
            assert rel.casefold() in paths and any(rel.casefold().startswith((PREFIX+r+'/').casefold()) for r in roots)
            entries.append(dict(path=rel,kind='recovery' if Path(rel).suffix.casefold()=='.mv_' else 'project'))
        warnings=list(p.get('warnings',[]))
        warnings.append('Historical unresolved references and same-name candidates are not proven replacements. Only the selected local working set is fetched.')
        outside=[f['path'] for f in p['files'] if not any(f['path'].casefold().startswith((PREFIX+r+'/').casefold()) for r in ROOTS)]
        if outside:warnings.append('Includes installed MAGIX assets outside supported user paths. No Program Files mapping is created; compatible installed content or explicit relinking may still be required.')
        projects.append(dict(id=p['id'],name=p['name'],category='magix-project',files=len(p['files']),bytes=sum(f['bytes'] for f in p['files']),entryPoints=entries,aliasRoots=roots,notes=p.get('notes',[]),warnings=warnings,unsupportedSelectedPaths=outside))
    projects.sort(key=lambda p:(p['name'].casefold(),p['id']))
    assert len(projects)==43 and sum(len(p['entryPoints']) for p in projects)==276
    return dict(format='magix-launcher-index-v1',catalogId=cat['catalogId'],catalogPublishedUtc=cat.get('publishedUtc','2026-10-29T20:06:53.559180+00:00'),catalogSha256=CATALOG_SHA,source='Reviewed published MAGIX project families; frozen offline snapshot',projects=projects)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('catalog');parser.add_argument('destination');args=parser.parse_args()
    data=(json.dumps(build(args.catalog),ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    with open(args.destination,'xb') as out:out.write(data)
    print(json.dumps(dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())))
