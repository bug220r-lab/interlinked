"""Synthetic regression for Windows' EA/RECALL_ON_OPEN attribute alias.

No installed files are changed. API results are injected to cover both aliases,
failed queries and independently forbidden attributes on an actual fixture path.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import importlib.util
import json
import os
import stat
import sys
import uuid


HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve() / ('path-attributes-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    leaf = root / 'ordinary.bin'
    leaf.write_bytes(b'synthetic local fixture')
    checks = []

    def check(ok, label):
        assert ok, label
        checks.append(label)

    def reject(fn, label):
        try:
            fn()
        except (ValueError, OSError):
            checks.append(label)
        else:
            raise AssertionError(label)

    class Function:
        def __init__(self, value):
            self.value = value
            self.calls = []
        def __call__(self, path):
            self.calls.append(path)
            return self.value

    original_lstat = os.lstat
    real = original_lstat(leaf)
    for name in ('interlinked_gui', 'interlinked_hub'):
        module = load(name)
        # This test targets the Windows API interpretation and requires Windows.
        check(os.name == 'nt', name + ': native Windows fixture')
        module.ordinary(leaf)
        check(True, name + ': actual ordinary fixture is accepted')
        def lstat(path, *, attrs=0x40020):
            if str(path) in (str(leaf), module.fs(leaf)):
                return SimpleNamespace(st_mode=real.st_mode, st_nlink=real.st_nlink,
                                       st_file_attributes=attrs)
            return original_lstat(path)
        query = Function(0x20)
        with patch.object(module.ctypes, 'WinDLL', return_value=SimpleNamespace(GetFileAttributesW=query)), \
             patch.object(module.os, 'lstat', side_effect=lstat):
            module.ordinary(leaf)
            check(bool(query.calls), name + ': lstat EA is checked with path API and accepted')
        for bad in (0x400, 0x1000, 0x400000):
            query = Function(0x20)
            with patch.object(module.ctypes, 'WinDLL', return_value=SimpleNamespace(GetFileAttributesW=query)), \
                 patch.object(module.os, 'lstat', side_effect=lambda p, bad=bad: lstat(p, attrs=0x40020 | bad)):
                reject(lambda: module.ordinary(leaf), name + ': independent lstat rejection retained ' + hex(bad))
        for bad in (0x400, 0x1000, 0x40000, 0x400000):
            query = Function(0x20 | bad)
            with patch.object(module.ctypes, 'WinDLL', return_value=SimpleNamespace(GetFileAttributesW=query)), \
                 patch.object(module.os, 'lstat', side_effect=lstat):
                reject(lambda: module.ordinary(leaf), name + ': native API rejection retained ' + hex(bad))
        query = Function(0xffffffff)
        with patch.object(module.ctypes, 'WinDLL', return_value=SimpleNamespace(GetFileAttributesW=query)), \
             patch.object(module.os, 'lstat', side_effect=lstat):
            reject(lambda: module.ordinary(leaf), name + ': failed native query fails closed')
        with patch.object(module.ctypes, 'WinDLL', side_effect=AssertionError('Unexpected native query')):
            check(module.path_attributes(leaf, SimpleNamespace(st_file_attributes=0x20)) == 0x20,
                  name + ': unambiguous attributes need no extra query')
        with patch.object(module.os, 'lstat', side_effect=lambda p: SimpleNamespace(st_mode=stat.S_IFLNK,
                                   st_nlink=1, st_file_attributes=0x20) if str(p) in (str(leaf), module.fs(leaf)) else original_lstat(p)):
            reject(lambda: module.ordinary(leaf), name + ': symbolic link mode remains refused')

    receipt = dict(format='interlinked-path-attribute-tests-v1', checks=checks,
                   passed=len(checks), fixture=str(root))
    (root / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=len(checks), receipt=str(root / 'receipt.json'))))


if __name__ == '__main__':
    main()
