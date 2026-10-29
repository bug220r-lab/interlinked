"""Verify selected source bytes without loading application modules or writing."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent

def main():
    manifest = json.loads((ROOT/'SOURCE-MANIFEST.json').read_text('utf-8-sig'))
    assert manifest['format'] == 'interlinked-source-snapshot-v1'
    seen=set()
    total=0
    for row in manifest['files']:
        relative=PurePosixPath(row['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        assert row['path'].casefold() not in seen
        seen.add(row['path'].casefold())
        path=ROOT.joinpath(*relative.parts)
        data=path.read_bytes()
        assert len(data)==row['bytes'], 'Size differs: '+row['path']
        assert hashlib.sha256(data).hexdigest()==row['sha256'], 'SHA-256 differs: '+row['path']
        text=data.decode('utf-8-sig')
        assert '\0' not in text, 'Unexpected binary: '+row['path']
        if path.suffix=='.py': ast.parse(text,filename=str(path))
        total+=len(data)
    expected=manifest['summary']
    assert len(seen)==expected['exactTextFiles'] and total==expected['bytes']
    extension_count=0
    extension_path=ROOT/'MAGIX-EXTENSION-MANIFEST.json'
    if extension_path.exists():
        extension=json.loads(extension_path.read_text('utf-8-sig'))
        assert extension['format']=='interlinked-magix-extension-source-v1'
        for row in extension['files']:
            relative=PurePosixPath(row['path'])
            assert not relative.is_absolute() and '..' not in relative.parts
            data=ROOT.joinpath(*relative.parts).read_bytes()
            assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'], 'Extension differs: '+row['path']
            extension_count+=1
    distribution_count=0
    distribution_path=ROOT/'DISTRIBUTION-MANIFEST.json'
    if distribution_path.exists():
        distribution=json.loads(distribution_path.read_text('utf-8-sig'))
        assert distribution['format']=='interlinked-distribution-manifest-v1'
        distribution_seen=set()
        for row in distribution['files']:
            relative=PurePosixPath(row['path'])
            assert not relative.is_absolute() and '..' not in relative.parts
            assert row['path'].casefold() not in distribution_seen
            distribution_seen.add(row['path'].casefold())
            data=ROOT.parent.joinpath(*relative.parts).read_bytes()
            assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'], 'Distribution differs: '+row['path']
        distribution_count=len(distribution_seen)
    print(json.dumps(dict(status='PASS',exactImportedTextFiles=len(seen),bytes=total,
                         extensionFilesVerified=extension_count,
                         distributionFilesVerified=distribution_count,
                         sealedClientFilesProvided=manifest['sealedClient']['providedFiles'],
                         sealedClientRequired=manifest['sealedClient']['requiredFiles'],
                         scope='Imported source hashes and Python syntax only; no deployment or runtime test.')))

if __name__=='__main__': main()
