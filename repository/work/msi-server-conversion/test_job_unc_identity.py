"""Local ownership regression only: no fetch, job execution, scheduler or network.

The real client constructs its own OWNER and serializes state identity. This is
intentionally different from the old tests' hand-written literal share paths.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

BASE = Path(__file__).absolute().parents[2]
RUNNER = BASE / 'outputs/Project-Library-2026-10-29/Server-Setup/msi_job_runner.py'
parser = argparse.ArgumentParser()
parser.add_argument('--expect-old-rejection', action='store_true')
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('review_job_unc_identity', RUNNER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
lib = worker.lib
fixture = BASE / 'work/msi-server-conversion' / ('job-unc-identity-' + uuid.uuid4().hex)
cache = fixture / 'Cache'
jobs = fixture / 'Jobs'
pid = 'client-created-share-root-fixture'

# Fail if a future implementation unexpectedly tries any remote filesystem IO.
original_check_path = lib.check_path
def local_only(path):
    if str(path).startswith(('\\\\', '//')):
        raise AssertionError('This test must not touch a network path: ' + str(path))
    return original_check_path(path)
lib.check_path = local_only
def no_job(*args, **kwargs):
    raise AssertionError('This regression must never execute any job process')
worker.run_process_tree = no_job

client = lib.Client(SimpleNamespace(library=worker.LIBRARY, returns=worker.RETURNS,
                                   cache=str(cache), test_local=False))
client.catalog = {'catalogId': worker.CATALOG}
project = client.cache_root(pid, create=True)
client.project = {'id': pid, 'files': []}
state = dict(format='project-library-cache-v1', **client.identity(pid), status='READY',
             baselineHead=None, savedHead=None, files=[], originals=[], directories=[])
client.save_state(state)
owner_path = project / 'OWNER.json'
state_path = project / 'state.json'
owner = lib.read_json(owner_path)
assert owner['library'] == worker.LIBRARY + '\\', owner
assert owner['returns'] == worker.RETURNS + '\\', owner
assert state['library'] == owner['library'] and state['returns'] == owner['returns']
report = dict(format='msi-job-unc-identity-regression-v1', fixtureRoot=str(fixture),
              runnerSha256=hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
              clientSha256=hashlib.sha256(Path(lib.__file__).read_bytes()).hexdigest(),
              engineGeneratedIdentity=client.identity(pid), networkAccessed=False,
              productionCacheAccessed=False, jobsExecuted=0, scheduledTasksRegistered=0,
              checks=[])
if args.expect_old_rejection:
    try:
        worker.cache_project(pid, cache)
    except RuntimeError as exc:
        assert str(exc) == 'Unexpected library/returns identity', str(exc)
        report['checks'].append('Actual client-created UNC-root owner rejected by old strict comparison')
        report['oldFailure'] = str(exc)
    else:
        raise AssertionError('Expected the old runner to reject client-created ownership')
else:
    assert worker.cache_project(pid, cache) == project
    report['checks'].append('Real Client.cache_root OWNER and Client.identity/save_state accepted')
    original_owner_bytes = owner_path.read_bytes()
    original_state_bytes = state_path.read_bytes()
    entry = project / 'Files/entry.py'
    entry.write_text("raise RuntimeError('This fixture must never execute')\n", encoding='utf-8')
    request = project / 'Files/spec.json'
    lib.atomic_json(request, dict(format='msi-job-spec-v1', projectId=pid, kind='python',
                                  entry='entry.py', workingDirectory='.', arguments=[]), replace=False)
    prepared = worker.prepare(request, cache, jobs)
    job = lib.read_json(jobs / prepared['jobId'] / 'job.json')
    assert prepared['status'] == 'PREPARED_NOT_REGISTERED'
    assert job['cacheStateSha256'] == hashlib.sha256(original_state_bytes).hexdigest()
    assert lib.read_json(jobs / prepared['jobId'] / 'status.json')['status'] == 'PREPARED_NOT_REGISTERED'
    assert owner_path.read_bytes() == original_owner_bytes and state_path.read_bytes() == original_state_bytes
    report['checks'].append('Prepare accepts actual client identity, binds exact state SHA, and does not execute or alter cache metadata')

    def write_pair(o, s):
        lib.atomic_json(owner_path, o)
        client.save_state(s)

    for label, o, s in [
        ('legacy roots without trailing separators', dict(owner, library=worker.LIBRARY, returns=worker.RETURNS), dict(state, library=worker.LIBRARY, returns=worker.RETURNS)),
        ('mixed case and exactly one optional trailing root separator', dict(owner, library=worker.LIBRARY.lower(), returns=worker.RETURNS.lower()+'\\'), dict(state, library=worker.LIBRARY+'\\', returns=worker.RETURNS)),
    ]:
        write_pair(o, s)
        assert worker.cache_project(pid, cache) == project
        report['checks'].append('Accepted ' + label)

    rejected = 0
    for key, expected in [('library', worker.LIBRARY), ('returns', worker.RETURNS)]:
        malformed = [expected+'\\\\', expected+'\\.', expected+'\\..', expected+'\\child',
                     expected+'/', expected+' ', expected+'.', expected+':stream',
                     expected.replace('MSI', '192.168.247.1'), expected.replace('MSI', 'localhost'),
                     expected.replace('MSI', 'THIS-MACHINE'), expected.replace('MSI', 'MSI.local'),
                     expected.replace('MSI\\', 'MSI\\\\'), '//MSI/'+expected.split('\\')[-1],
                     '\\\\?\\UNC\\'+expected[2:], None, 12, [], {}]
        for location in ('owner', 'state'):
            for value in malformed:
                o = dict(owner); s = dict(state)
                (o if location == 'owner' else s)[key] = value
                write_pair(o, s)
                try:
                    worker.cache_project(pid, cache)
                except RuntimeError:
                    rejected += 1
                else:
                    raise AssertionError(f'Accepted invalid {location} {key}: {value!r}')
    report['checks'].append(f'{rejected} malformed or alternate owner/state share values rejected (IPs, aliases, traversal, subdirectories, extra separators and nonstrings)')
    for label, extra in [('pending save', {'pendingSave': {'fixture': True}}), ('released cache', {'status': 'RELEASED'})]:
        write_pair(owner, dict(state, **extra))
        try:
            worker.cache_project(pid, cache)
        except RuntimeError:
            report['checks'].append('Preserved refusal of ' + label)
        else:
            raise AssertionError('Accepted ' + label)
    write_pair(owner, state)
    assert worker.cache_project(pid, cache) == project
report['status'] = 'PASS'
result = fixture / 'result.json'
result.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(dict(report, evidence=str(result)), indent=2))
