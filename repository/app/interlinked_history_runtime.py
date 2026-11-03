"""Load independently packaged history dependencies without changing sealed Python."""
from pathlib import Path
import hashlib
import json
import os
import sys
import importlib.util

_loaded = False
EXPECTED_MANIFESTS = {
    (3, 12): 'e31b8ad42386acb5ff820efc6c9dc7ac6b8bc0c7b5ebbffaeed27e4c328d026e',
    (3, 13): '95de595745770093e36a2db7b8b56ae4bbf18fc76f5e3a5eb4b915b1fbd28236',
}
GIT_MANIFEST_SHA256 = 'e9a869b77285c741ad786edf0deb2fc403231b13d3fff4f39068ee0bd4559c78'


def load_git():
    """Return only the independently sealed passive Git dependency, if installed."""
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'INTERLINKED' / 'HistoryRuntime' / 'git-2.56.0.4'
    manifest = base / 'GIT-CHECKSUMS.json'
    if not manifest.exists():
        if Path(__file__).parent.name == 'app' and Path(__file__).parent.parent.name == 'repository':
            import shutil
            return shutil.which('git')  # Existing synthetic source fixtures only.
        return None
    import interlinked_configuration as config
    raw = config.read_bytes(manifest, 4 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != GIT_MANIFEST_SHA256:
        raise ValueError('History Git dependency manifest differs.')
    value = json.loads(raw)
    if value.get('format') != 'interlinked-history-git-v1' or value.get('platform') != 'windows-x64' or value.get('executable') != 'cmd/git.exe':
        raise ValueError('History Git dependency identity differs.')
    expected = {'GIT-CHECKSUMS.json'}
    for row in value['files']:
        rel = Path(row['path'])
        if rel.is_absolute() or '..' in rel.parts or ':' in str(rel) or rel.as_posix().casefold() in {x.casefold() for x in expected}:
            raise ValueError('Invalid Git dependency path.')
        expected.add(rel.as_posix())
        path = config.ordinary(base / rel)
        if path.stat().st_size != row['bytes']:
            raise ValueError('History Git dependency size differs.')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != row['sha256']:
            raise ValueError('History Git dependency hash differs.')
    actual = set()
    for path in base.rglob('*'):
        config.ordinary(path)
        if path.is_file():
            actual.add(path.relative_to(base).as_posix())
    if actual != expected:
        raise ValueError('Unexpected history Git dependency files.')
    return str(config.ordinary(base / 'cmd/git.exe'))


def load_service():
    """Load only sealed sibling modules under the launchers' isolated (-I) Python."""
    for name in ('interlinked_configuration', 'interlinked_history_store', 'interlinked_history_import',
                 'interlinked_history_gateway', 'interlinked_history_service'):
        if name in sys.modules:
            continue
        path = Path(__file__).with_name(name + '.py')
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules['interlinked_history_service']


def load_dependencies():
    global _loaded
    if _loaded:
        return
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'INTERLINKED' / 'HistoryRuntime' / ('py%d%d' % sys.version_info[:2])
    manifest = base / 'DEPENDENCIES.json'
    if not manifest.exists():
        if Path(__file__).parent.name == 'app' and Path(__file__).parent.parent.name == 'repository':
            return  # Isolated source tests may supply their own synthetic runtime.
        raise ValueError('Install the reviewed history dependency runtime first.')
    import interlinked_configuration as config
    raw = config.read_bytes(manifest, 4 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFESTS.get(sys.version_info[:2]):
        raise ValueError('History dependency manifest differs from the reviewed package.')
    value = json.loads(raw)
    if value.get('format') != 'interlinked-history-dependencies-v1' or value.get('python') != list(sys.version_info[:2]):
        raise ValueError('History dependency runtime differs.')
    expected = {'DEPENDENCIES.json'}
    for row in value['files']:
        rel = Path(row['path'])
        if rel.is_absolute() or '..' in rel.parts or ':' in str(rel):
            raise ValueError('Invalid dependency path.')
        if rel.as_posix().casefold() in {x.casefold() for x in expected}:
            raise ValueError('Duplicate dependency path.')
        expected.add(rel.as_posix())
        path = config.ordinary(base / rel)
        if path.stat().st_size != row['bytes']:
            raise ValueError('History dependency size differs.')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != row['sha256']:
            raise ValueError('History dependency hash differs.')
    actual = set()
    for path in base.rglob('*'):
        config.ordinary(path)
        if path.is_file(): actual.add(path.relative_to(base).as_posix())
    if actual != expected:
        raise ValueError('Unexpected history dependency files.')
    for name in ('numpy', 'pypdf'):
        module = sys.modules.get(name)
        if module is not None and not Path(module.__file__).resolve().is_relative_to(base.resolve()):
            raise ValueError('History dependency was loaded from an unreviewed location.')
    sys.path.insert(0, str(base))
    _loaded = True
