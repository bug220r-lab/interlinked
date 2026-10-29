"""Read-only import plus mocked process-enumeration boundary checks.
No real process query, app launch, network, junction or ACL operations.
"""
from pathlib import Path
import ctypes
import hashlib
import importlib.util
import json
import sys
import uuid

here = Path(__file__).parent
source = here / 'magix_workspace.py'
spec = importlib.util.spec_from_file_location('reviewed_magix_workspace', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeFunction:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class FakeKernel:
    def __init__(self, first, next_, error):
        self.error = error
        self.closed = False
        self.CreateToolhelp32Snapshot = FakeFunction(lambda *args: 123)
        self.Process32FirstW = FakeFunction(lambda handle, entry: first(entry))
        self.Process32NextW = FakeFunction(lambda handle, entry: next_(entry))
        self.CloseHandle = FakeFunction(self.close)
    def close(self, _): self.closed = True; return 1


def named(entry):
    entry._obj.name = 'Videodeluxe.exe'
    entry._obj.pid = 456
    return 1


checks = []
original_dll = ctypes.WinDLL
original_error = ctypes.get_last_error
try:
    scenarios = [
        ('First enumeration error cannot mean closed', lambda p: 0, lambda p: 0, 5, True),
        ('Next enumeration error cannot mean closed', named, lambda p: 0, 5, True),
        ('Normal empty process snapshot is accepted', lambda p: 0, lambda p: 0, 18, False),
        ('Named MAGIX process remains visible at normal end', named, lambda p: 0, 18, False),
    ]
    for label, first, next_, error, must_fail in scenarios:
        fake = FakeKernel(first, next_, error)
        ctypes.WinDLL = lambda *args, **kwargs: fake
        ctypes.get_last_error = lambda: fake.error
        failed = False
        rows = None
        try: rows = module.magix_processes()
        except OSError: failed = True
        assert failed == must_fail, label
        assert fake.closed, 'Snapshot handle not closed: ' + label
        if not must_fail:
            if label.startswith('Normal empty'): assert rows == []
            else: assert rows == [{'pid': 456, 'name': 'Videodeluxe.exe'}]
        checks.append(label)
finally:
    ctypes.WinDLL = original_dll
    ctypes.get_last_error = original_error

class UnusedEngine:
    def __getattr__(self, name): raise AssertionError('Unexpected action before confirmation: ' + name)

workspace = module.Workspace(module.Config(Path('unused-kit'), Path('unused-cache'), Path('unused-legacy')), UnusedEngine(), processes=lambda: (_ for _ in ()).throw(AssertionError('Unexpected process query')))
try: workspace.release('YES')
except RuntimeError as exc: assert 'complete project ID' in str(exc)
else: raise AssertionError('Wrong release confirmation accepted')
checks.append('Wrong release confirmation performs no cache or process action')

result = {
    'Status': 'PASS', 'Checks': checks, 'Count': len(checks),
    'CoreSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'RealProcessQueries': False, 'ProductionFilesChanged': False,
    'NetworkAccess': False, 'AclChanges': False, 'MagixLaunched': False,
}
receipt = here / ('independent-process-guards-' + uuid.uuid4().hex + '.json')
with receipt.open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(dict(result, Receipt=str(receipt)), indent=2))
