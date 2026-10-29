"""One-shot synthetic verification on THIS-MACHINE; no production cache use.

Run from the local handoff kit with the shipped client/runtime and published
New-PC-Verification-Plan.json. Does not authenticate, change settings, execute
project code, or delete files itself. Only the shipped client releases its own
verified synthetic workspace. Receipts and failed partial work are retained.
"""
from __future__ import annotations
import datetime
import contextlib
import hashlib
import importlib.util
import json
import msvcrt
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import time
import types
import uuid

ENGINE_SHA = '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d'
CHECKSUMS_SHA = '362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc'
PROJECT = 'new-pc-smoke'
PREFIX = 'Drive_C/Connection-Test/'
SOURCES = {
    'unchanged.txt': b'UNCHANGED SYNTHETIC VERIFICATION FILE\r\n',
    'edit-me.txt': b'ORIGINAL SYNTHETIC CONTENT\r\n',
    'empty.bin': b'',
    'nested/events.jsonl': b'{"synthetic":true,"complete":1}\n{"incomplete":',
    'nested/binary.dat': bytes(range(256)),
}
FETCHED = dict(SOURCES, **{'nested/events.jsonl': b'{"synthetic":true,"complete":1}\n'})
SAVED = dict(FETCHED, **{'edit-me.txt': b'EDITED ON THE NEW PC: SYNTHETIC CONTENT ONLY\r\n',
                           '.fixture-note.txt': b'NEW SYNTHETIC FILE: NO USER DATA\r\n'})

def need(condition, message):
    if not condition:
        raise RuntimeError(message)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def plain(path, directory=None):
    """Check existing ancestors without following a junction/symlink."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & (0x400|0x1000|0x40000|0x400000)),
             'Link/reparse path refused: ' + str(part))
    if directory is not None:
        info = path.lstat()
        need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
             'Unexpected path type: ' + str(path))
    return path

def small(path, maximum=65536):
    path = plain(path, False)
    need(path.stat().st_size <= maximum, 'Unexpectedly large fixture/control file')
    return path.read_bytes()

def read_json(path):
    data = json.loads(small(path).decode('utf-8-sig'))
    need(isinstance(data, dict), 'Expected metadata object')
    return data

def write_json_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())

def verified_client(client):
    client = plain(client, True)
    engine = plain(client/'project_library.py', False)
    need(sha(engine.read_bytes()) == ENGINE_SHA, 'Shipped client engine differs from the reviewed version')
    manifest_path = client/'HELPER-CHECKSUMS.json'
    need(sha(small(manifest_path)) == CHECKSUMS_SHA, 'Shipped client checksum manifest differs')
    manifest = read_json(manifest_path)
    need(manifest.get('format') == 'project-library-client-checksums-v1' and len(manifest['files']) == 52,
         'Unexpected client package manifest')
    for row in manifest['files']:
        name = row['path'].replace('\\', '/')
        need(not name.startswith('/') and ':' not in name and all(p not in ('', '.', '..') for p in name.split('/')),
             'Unsafe client manifest path')
        path = plain(client/name, False)
        need(path.stat().st_size == row['bytes'], 'Client file length differs: ' + name)
        with path.open('rb') as stream:
            need(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'], 'Client hash differs: ' + name)
    need(read_json(client/'Settings.json').get('server') == 'MSI', 'Keep the canonical MSI client server setting')
    spec = importlib.util.spec_from_file_location('reviewed_verification_client', engine)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def validate_plan(plan):
    need(plan.get('format') == 'project-library-new-pc-verification-v1', 'Unexpected verification plan')
    name = plan.get('fixtureName', '')
    need(re.fullmatch(r'Verification-New-PC-[a-f0-9]{32}', name) is not None, 'Unsafe fixture identifier')
    need(plan.get('projectId') == PROJECT and plan.get('catalogId') == name, 'Fixture project identity differs')
    need(plan.get('sourceHashes') == {PREFIX+k: sha(v) for k, v in SOURCES.items()}, 'Fixture expected hashes differ')
    need(plan.get('sourceBytes') == sum(map(len, SOURCES.values())), 'Fixture byte total differs')
    for key in ('catalogSha256', 'ownerSha256'):
        need(re.fullmatch(r'[a-f0-9]{64}', plan.get(key, '')) is not None, 'Invalid control-file digest')
    return name

def run(plan, client, kit, *, local_test_roots=None):
    """Internal test override is never exposed by the user-facing CLI."""
    kit = plain(kit, True)
    need(not str(kit).startswith('\\\\'), 'Run from a local extracted kit')
    fixture = validate_plan(plan)
    machine = platform.node().upper()
    if local_test_roots is None:
        need(os.name == 'nt' and machine == 'THIS-MACHINE', 'Run this only on the new PC named THIS-MACHINE')
        library = Path('\\\\MSI\\ProjectLibrary\\'+fixture)
        returns = Path('\\\\MSI\\ProjectReturns\\'+fixture)
    else:
        library, returns = map(Path, local_test_roots)
        need(library.parent == kit and returns.parent == kit, 'Local fixture roots must be direct children of the test kit')
    run_id = uuid.uuid4().hex
    evidence = kit/('Verification-Run-'+run_id)
    evidence.mkdir()  # A fresh UUID directory; no prior state adoption or overwrite.
    cache = evidence/'Cache'
    report = {'format': 'project-library-new-pc-roundtrip-v1', 'runId': run_id,
              'startedUtc': utc(), 'computer': machine, 'fixtureName': fixture,
              'mode': 'ACTUAL_NEW_PC_SMB' if local_test_roots is None else 'LOCAL_FIXTURE_ONLY',
              'status': 'RUNNING', 'library': str(library), 'returns': str(returns),
              'cache': str(cache), 'receiptPath': str(evidence/'result.json'), 'steps': [],
              'productionProjectsAccessed': False, 'productionCacheChanged': False,
              'settingsChanged': False, 'authenticationChanged': False, 'sshTested': False}
    def checkpoint():
        temporary = evidence/('result-'+uuid.uuid4().hex+'.tmp')
        write_json_new(temporary, report)
        os.replace(temporary, evidence/'result.json')
    checkpoint()
    print('Synthetic test only. Receipt folder: '+str(evidence), flush=True)
    try:
        engine = verified_client(client)
        report['clientFilesVerified'] = 52
        report['clientEngineSha256'] = ENGINE_SHA
        plain(library, True); plain(returns, True)
        with engine.reader(library/'.library-access.lock'), engine.exclusive(returns/'.verification-run.lock'):
            for base in (library, returns):
                need(sha(small(base/'FIXTURE-OWNER.json')) == plan['ownerSha256'], 'Isolated fixture ownership digest differs')
                owner = read_json(base/'FIXTURE-OWNER.json')
                need(owner == {'format': 'project-library-verification-fixture-v1', 'fixtureName': fixture,
                               'catalogId': fixture, 'projectId': PROJECT, 'syntheticOnly': True}, 'Fixture ownership differs')
            allowed_returns = {'FIXTURE-OWNER.json', 'FIXTURE-README.txt', '.verification-run.lock'}
            need({p.name for p in returns.iterdir()} == allowed_returns,
                 'Fixture Returns contains prior/unexpected state. Preserve it; request a fresh fixture instead of rerunning.')
            need(sha(small(library/'catalog.json')) == plan['catalogSha256'], 'Fixture catalog digest differs')
            catalog = read_json(library/'catalog.json')
            need(catalog.get('format') == 'project-library-catalog-v1' and catalog.get('catalogId') == fixture,
                 'Fixture catalog identity differs')
            need(len(catalog['projects']) == 1 and catalog['projects'][0]['id'] == PROJECT, 'Unexpected fixture project')
            rows = catalog['projects'][0]['files']
            expected_paths = {PREFIX+k for k in SOURCES}
            need(len(rows) == 5 and {r['path'] for r in rows} == expected_paths, 'Unexpected fixture file membership')
            for row in rows:
                leaf = row['path'][len(PREFIX):]
                need(row['sourceRelative'] == 'Sources/'+row['path'] and row['bytes'] == len(SOURCES[leaf])
                     and row['kind'] == ('jsonl' if leaf.endswith('.jsonl') else 'file'), 'Fixture file metadata differs')
            def check_sources():
                files, _ = engine.scan(library/'Sources')
                need(set(files) == expected_paths, 'Fixture source membership differs')
                for path, digest in plan['sourceHashes'].items():
                    with engine.reader(library/'Sources'/path) as stream:
                        expected_bytes = len(SOURCES[path[len(PREFIX):]])
                        need(engine.fingerprint(stream)['bytes'] == expected_bytes, 'Fixture source changed: '+path)
                        need(sha(stream.read(expected_bytes+1)) == digest, 'Fixture source changed: '+path)
            check_sources()
            workspace = cache/PROJECT/'Files'
            def check_workspace(expected):
                files, _ = engine.scan(workspace)
                need(set(files) == {PREFIX+k for k in expected}, 'Synthetic workspace membership differs')
                for leaf, payload in expected.items():
                    need(small(workspace/PREFIX/leaf) == payload, 'Synthetic workspace bytes differ: '+leaf)
            def command(action, expected, label=None):
                number = len(report['steps'])+1
                label = label or action
                stdout = evidence/f'{number:02d}-{label}.stdout.json'
                stderr = evidence/f'{number:02d}-{label}.stderr.log'
                argv = [str(Path(client)/'Runtime/python.exe'), '-B', '-X', 'utf8',
                        str(Path(client)/'project_library.py'), '--library', str(library),
                        '--returns', str(returns), '--cache', str(cache)]
                if local_test_roots is not None:
                    argv.append('--test-local')
                argv += [action, PROJECT]
                if action == 'release':
                    argv += ['--confirm', PROJECT]
                print(f'{number}. {label}...', flush=True)
                with stdout.open('xb') as out, stderr.open('xb') as err:
                    process = subprocess.Popen(argv, stdout=out, stderr=err,
                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    started = time.monotonic()
                    try:
                        while True:
                            try:
                                code = process.wait(timeout=5)
                                break
                            except subprocess.TimeoutExpired:
                                print(f'   {label} is still running...', flush=True)
                                need(time.monotonic()-started < 90, 'Client step exceeded the 90-second test limit')
                    except BaseException:
                        process.kill()  # Only this runner's own isolated client child.
                        process.wait()
                        raise
                response = read_json(stdout)
                report['steps'].append({'operation': label, 'exitCode': code, 'status': response.get('status'),
                                        'stdout': str(stdout), 'stderr': str(stderr)})
                checkpoint()
                need(code == 0 and response.get('status') == expected,
                     label+' failed; see its retained stdout/stderr receipts')
                need(Path(response['workspacePath']) == workspace, 'Client returned an unexpected workspace')
                return response
            command('fetch', 'FETCH_VERIFIED')
            check_workspace(FETCHED)
            # Change one known synthetic file under a held write lock; do not delete.
            edited = plain(workspace/PREFIX/'edit-me.txt', False)
            with engine.reader(edited) as current:
                identity = engine.fingerprint(current)
            with contextlib.ExitStack() as guards:
                # Freeze each workspace ancestor against rename/reparse replacement
                # while this wrapper performs its two bounded synthetic writes.
                for directory in (kit, evidence, cache, cache/PROJECT, workspace,
                                  workspace/'Drive_C', workspace/PREFIX):
                    guards.enter_context(engine.directory_guard(directory))
                need(edited.stat().st_nlink == 1, 'Synthetic edit target is unexpectedly hard-linked')
                handle = engine.open_handle(edited, 0xC0000000, 0, 3, 0x00300000)
                try:
                    need(engine.fingerprint(types.SimpleNamespace(handle=handle)) == identity,
                         'Synthetic edit handle identity changed')
                    descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                except BaseException:
                    engine.native().CloseHandle(handle)
                    raise
                # fdopen owns the one native handle after successful conversion.
                with os.fdopen(descriptor, 'r+b', buffering=0) as stream:
                    need(stream.read() == FETCHED['edit-me.txt'], 'Synthetic edit target changed')
                    stream.seek(0); stream.write(SAVED['edit-me.txt']); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
                new = plain(workspace/PREFIX/'.fixture-note.txt')
                with new.open('xb') as stream:
                    stream.write(SAVED['.fixture-note.txt']); stream.flush(); os.fsync(stream.fileno())
            check_workspace(SAVED)
            command('save', 'SAVE_COMMITTED')
            command('verify', 'VERIFIED')
            command('release', 'RELEASED')
            need(not engine.scan(workspace)[0], 'Client release left unexpected files; preserving them')
            command('fetch', 'FETCH_VERIFIED', 'refetch-saved-version')
            check_workspace(SAVED)
            command('verify', 'VERIFIED', 'verify-refetched-version')
            command('release', 'RELEASED', 'release-refetched-version')
            need(not engine.scan(workspace)[0], 'Final release left unexpected files')
            check_sources()
            need(sha(Path(client, 'project_library.py').read_bytes()) == ENGINE_SHA, 'Client engine changed')
            report.update(status='PASS', sourceHashesUnchanged=True, savedVersionRefetchBytesVerified=True,
                          finalSyntheticWorkspaceEmpty=True, clientEngineUnchanged=True,
                          retainedSyntheticRemoteVersion=True)
    except BaseException as exc:
        report.update(status='INCOMPLETE', error=str(exc))
        print('Stopped safely: '+str(exc), flush=True)
    report['completedUtc'] = utc()
    try:
        checkpoint()
    except BaseException as exc:
        raise RuntimeError('Final receipt write failed; preserve evidence folder '+str(evidence)+': '+str(exc)) from exc
    print('Result: '+report['status']+'; receipt: '+str(evidence/'result.json'), flush=True)
    return report

def main():
    kit = None
    in_run = False
    try:
        candidate = plain(Path(__file__).parent, True)
        need(not str(candidate).startswith('\\\\'), 'Run from a local extracted kit')
        kit = candidate  # Only a verified local path may receive a failure receipt.
        plan = read_json(kit/'New-PC-Verification-Plan.json')
        in_run = True
        report = run(plan, kit/'Project-Library-Client', kit)
        return 0 if report['status'] == 'PASS' else 2
    except BaseException as exc:
        phase = 'VERIFICATION_OR_REPORTING' if in_run else 'PREFLIGHT'
        print(('Workflow/reporting stopped: ' if in_run else 'Blocked before workflow: ')+str(exc), flush=True)
        if kit is not None:
            try:
                path = kit/('Verification-Failure-'+uuid.uuid4().hex+'.json')
                write_json_new(path, {'format':'project-library-new-pc-roundtrip-v1', 'status':'BLOCKED',
                                     'phase':phase, 'computer':platform.node(), 'utc':utc(),
                                     'error':str(exc), 'productionProjectsAccessed':False,
                                     'productionCacheChanged':False})
                print('Failure receipt: '+str(path), flush=True)
            except OSError:
                pass
        return 2

if __name__ == '__main__':
    sys.exit(main())
