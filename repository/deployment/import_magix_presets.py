"""Add only the reviewed MAGIX custom presets and four League Gothic fonts.

No overwrite, deletion, cache/project edits, font-cache reset or service change.
The stock-definition comparison is a separate, read-only command.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types
import uuid
import winreg

HOST = 'THIS-MACHINE'
SID = 'S-1-5-21-1655665044-1352998972-69516628-1001'
VERSION = '20.0.1.80'
KIT = Path(r'C:\Users\Jude\Documents\MSI-Project-Library\Project-Library-Client')
ENGINE_SHA = '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d'
APP = Path(r'C:\Program Files (x86)\Steam\steamapps\common\MAGIX\Video deluxe 2021 Plus Steam Edition')
ROOTS = {'steam_content': APP / '_Content',
         'roaming_preset': Path(r'C:\Users\Jude\AppData\Roaming\MAGIX\Videodeluxe27_plus_steam'),
         'font': Path(r'C:\Windows\Fonts')}
FONT_KEY = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts'
FONTS = {
 'LeagueGothic-Italic.otf': (22648, '69df1c89650aaa8ffd52bb948267002b150551c0531dc738bfe67d41d06e31fc'),
 'LeagueGothic-CondensedRegular.otf': (25752, '4ff3ee92ec9a6a6bc94ebc36c439c09e607b2553e3bca81164f4b3b1658c76ba'),
 'LeagueGothic-CondensedItalic.otf': (30076, '0559ab8a0bf2de909158d3874aa69948852a34af367ebd763701ef0b6b6e4ddf'),
 'LeagueGothic-Regular.otf': (24472, 'a71d574cb5c8b4e780964356223ca504eeee0144202766f569982da9e73c0d47')}
SHA = re.compile(r'[0-9a-f]{64}\Z')

def need(ok, message):
    if not ok: raise RuntimeError(message)

def load_engine(path):
    for item in (path, *path.parents):
        attrs = getattr(os.lstat(item), 'st_file_attributes', 0)
        need(not attrs & (0x400|0x1000|0x40000|0x400000), 'Unsafe client dependency path')
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == ENGINE_SHA, 'Sealed client dependency hash mismatch')
    module = types.ModuleType('preset_native_client'); module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module

def validate_manifest(e, value):
    need(isinstance(value, dict), 'Manifest must be an object')
    need(value.get('format') == 'interlinked-magix-preset-import-v1', 'Wrong manifest format')
    need(re.fullmatch(r'[0-9a-f]{32}', value.get('packageId', '')) is not None, 'Invalid package ID')
    need((value.get('targetComputer'), value.get('targetUserSid'), value.get('expectedAppVersion')) == (HOST, SID, VERSION), 'Wrong destination identity/version')
    files = value.get('files'); need(isinstance(files, list) and len(files) == 28, 'Exactly 24 presets and 4 fonts are required')
    counts = {'effects': 0, 'titles': 0, 'transition': 0, 'roaming': 0, 'fonts': 0}
    targets = []; sources = []
    for row in files:
        need(isinstance(row, dict), 'Invalid file row')
        kind = row.get('kind'); need(kind in ROOTS, 'Unknown target kind')
        src = e.relative(row.get('sourceRelativePath')); rel = e.relative(row.get('targetRelativePath'))
        need(src.startswith('payload/'), 'Payload source must be under payload/')
        need(type(row.get('bytes')) is int and 0 < row['bytes'] <= 16*1024*1024, 'Invalid file length')
        need(isinstance(row.get('sha256'), str) and SHA.fullmatch(row['sha256']), 'Invalid file hash')
        parts = rel.split('/'); lower = rel.casefold()
        if kind == 'font':
            need(rel in FONTS and (row['bytes'], row['sha256']) == FONTS[rel], 'Unapproved font payload')
            need(row.get('fontRegistryName') == rel[:-4] + ' (TrueType)', 'Font registration name mismatch')
            counts['fonts'] += 1
        elif kind == 'roaming_preset':
            need(len(parts) == 3 and lower.startswith('presets/mxmpeg4intel/') and lower.endswith('.pru'), 'Unapproved roaming target')
            counts['roaming'] += 1
        elif len(parts) == 2 and parts[0].casefold() == 'uservideoeffects' and Path(parts[1]).suffix.casefold() in ('.ifx', '.moc'):
            counts['effects'] += 1
        elif len(parts) == 3 and lower.startswith('titles/standard/') and lower.endswith('.tfx'):
            counts['titles'] += 1
        elif lower == 'plugins/transitionpresets/faded.mxt': counts['transition'] += 1
        else: raise RuntimeError('Unapproved content target: ' + rel)
        targets.append(kind + '/' + rel); sources.append(src)
    e.paths_unique(targets); e.paths_unique(sources)
    need(counts == {'effects':16, 'titles':2, 'transition':1, 'roaming':5, 'fonts':4}, 'Unexpected import selection')
    need(sum(r['bytes'] for r in files) < 32*1024*1024, 'Package exceeds bounded import size')
    return files

def guard_tree(e, stack, path, seen, create=False, created=None):
    """Hold each ancestor against replacement before opening/creating its child."""
    for item in reversed((Path(path), *Path(path).parents)):
        key = str(item).casefold()
        if key in seen: continue
        if not e.exists(item):
            need(create, 'Missing directory: ' + str(item))
            os.mkdir(e.fs(item))
            if created is not None: created.append(str(item))
        stack.enter_context(e.directory_guard(item)); seen.add(key)

def checked_reader(e, stack, path, expected=None):
    need(e.regular(path).st_nlink == 1, 'Hard-linked file refused: ' + str(path))
    stream = stack.enter_context(e.reader(path))
    need(e.regular(path).st_nlink == 1, 'File identity changed: ' + str(path))
    fingerprint = e.fingerprint(stream)
    if expected:
        need(fingerprint['bytes'] == expected['bytes'], 'File length conflict: ' + str(path))
        need(e.digest(stream)[0] == expected['sha256'], 'File hash conflict: ' + str(path))
    return stream

def create_copy(e, source, target, expected):
    """CreateNew owns only the new file. Partial failures are retained and reported."""
    source.seek(0)
    with open(e.fs(target), 'xb', buffering=0) as out:
        h = hashlib.sha256(); count = 0
        while True:
            block = source.read(1024*1024)
            if not block: break
            need(out.write(block) == len(block), 'Short destination write')
            h.update(block); count += len(block)
        out.flush(); os.fsync(out.fileno())
    need((count, h.hexdigest()) == (expected['bytes'], expected['sha256']), 'Source changed during copy')

def read_reg(hive):
    result = {}
    try:
        with winreg.OpenKey(hive, FONT_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            index = 0
            while True:
                try: name, value, kind = winreg.EnumValue(key, index)
                except OSError as exc:
                    if exc.winerror == 259: break
                    raise
                index += 1
                if 'leaguegothic' in name.replace(' ', '').casefold() or (isinstance(value, str) and 'leaguegothic' in value.replace(' ', '').casefold()):
                    result[name] = (value, kind)
    except FileNotFoundError: pass
    return result

def font_registry_check(rows, machine, user):
    expected = {r['fontRegistryName']: r['targetRelativePath'] for r in rows if r['kind'] == 'font'}
    for table in (machine, user):
        for name, (value, kind) in table.items():
            need(name in expected and kind == winreg.REG_SZ, 'Conflicting League Gothic registration: ' + name)
            filename = expected[name]
            need(value.casefold() in (filename.casefold(), str(ROOTS['font'] / filename).casefold()), 'Conflicting League Gothic registration target: ' + name)
            if table is user:
                need(value.casefold() == str(ROOTS['font'] / filename).casefold(), 'Per-user League Gothic registration needs review: ' + name)

def register_fonts(rows, report):
    fonts = [r for r in rows if r['kind'] == 'font']
    font_registry_check(rows, read_reg(winreg.HKEY_LOCAL_MACHINE), read_reg(winreg.HKEY_CURRENT_USER))
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, FONT_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        for row in fonts:
            name = row['fontRegistryName']; filename = row['targetRelativePath']
            try: value, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, filename)
                report['registrationsAdded'].append(name)
            else:
                need(kind == winreg.REG_SZ and value.casefold() in (filename.casefold(), str(ROOTS['font'] / filename).casefold()), 'Font registry changed concurrently')
            value, kind = winreg.QueryValueEx(key, name)
            need(kind == winreg.REG_SZ and value.casefold() in (filename.casefold(), str(ROOTS['font'] / filename).casefold()), 'Font registry readback failed')
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    gdi.AddFontResourceW.argtypes = [ctypes.c_wchar_p]; gdi.AddFontResourceW.restype = ctypes.c_int
    for row in fonts:
        count = gdi.AddFontResourceW(str(ROOTS['font'] / row['targetRelativePath']))
        need(count > 0, 'Windows could not load font: ' + row['targetRelativePath'])
        report['fontsLoaded'].append({'file': row['targetRelativePath'], 'count': count})
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    result = ctypes.c_size_t()
    report['fontChangeBroadcastSucceeded'] = bool(user32.SendMessageTimeoutW(0xffff, 0x1d, 0, 0, 2, 3000, ctypes.byref(result)))
    font_registry_check(rows, read_reg(winreg.HKEY_LOCAL_MACHINE), read_reg(winreg.HKEY_CURRENT_USER))

def environment_check(apply):
    command = r"$ErrorActionPreference='Stop';$id=[Security.Principal.WindowsIdentity]::GetCurrent();$p=New-Object Security.Principal.WindowsPrincipal($id);$v=(Get-Item -LiteralPath 'C:\Program Files (x86)\Steam\steamapps\common\MAGIX\Video deluxe 2021 Plus Steam Edition\Videodeluxe.exe').VersionInfo;[pscustomobject]@{computer=$env:COMPUTERNAME;sid=$id.User.Value;admin=$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);version=(@($v.FileMajorPart,$v.FileMinorPart,$v.FileBuildPart,$v.FilePrivatePart)-join '.');magix=@(Get-Process | Where-Object {$_.ProcessName -match '^Videodeluxe|^Video_deluxe'}).Count}|ConvertTo-Json -Compress"
    result = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], capture_output=True, text=True, timeout=30)
    need(result.returncode == 0, 'Could not verify destination identity/application: ' + result.stderr.strip())
    value = json.loads(result.stdout)
    need(value['computer'].casefold() == HOST.casefold() and value['sid'] == SID, 'Run on THIS-MACHINE as the existing Jude account')
    need(not apply or value['admin'], 'Apply requires UAC elevation as the same Jude account')
    need(value['version'] == VERSION, 'MAGIX version differs from reviewed 20.0.1.80')
    need(value['magix'] == 0, 'Close MAGIX before importing presets/fonts')
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\' + SID, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        profile, _ = winreg.QueryValueEx(key, 'ProfileImagePath')
    need(os.path.normpath(os.path.expandvars(profile)).casefold() == r'c:\users\jude', 'Jude profile mapping changed')
    return value

def execute(e, package, manifest_sha, apply, roots=None, env_check=environment_check, reg_read=read_reg, register=register_fonts):
    roots = roots or ROOTS
    report = {'format':'interlinked-magix-preset-import-receipt-v1', 'runId':uuid.uuid4().hex,
              'status':'PREFLIGHT', 'apply':apply, 'added':[], 'skipped':[], 'createdDirectories':[],
              'registrationsAdded':[], 'fontsLoaded':[], 'sourceOriginalsModified':False,
              'projectCachesModified':False, 'existingFilesOverwritten':False}
    with contextlib.ExitStack() as stack:
        seen = set(); guard_tree(e, stack, package, seen)
        receipt = package / ('Import-Receipt-' + report['runId'] + '.json')
        # Serialize this package; no project/cache lock is touched.
        lock = package / '.preset-import.lock'
        if e.exists(lock): need(e.regular(lock).st_nlink == 1, 'Hard-linked importer lock refused')
        stack.enter_context(e.exclusive(lock, create=True))
        try:
            stream = checked_reader(e, stack, package / 'manifest.json')
            need(e.fingerprint(stream)['bytes'] <= 1024*1024, 'Manifest is too large')
            raw = stream.read(); need(hashlib.sha256(raw).hexdigest() == manifest_sha, 'Manifest hash mismatch')
            value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=e.unique_keys)
            rows = validate_manifest(e, value); report['packageId'] = value['packageId']; report['manifestSha256'] = manifest_sha
            report['environment'] = env_check(apply)
            sources = {}; missing = []
            for row in rows:
                source = package.joinpath(*e.relative(row['sourceRelativePath']).split('/'))
                target = roots[row['kind']].joinpath(*e.relative(row['targetRelativePath']).split('/'))
                guard_tree(e, stack, source.parent, seen)
                sources[row['sourceRelativePath']] = checked_reader(e, stack, source, row)
                # Existing ancestors are guarded now; missing category folders are created only after all conflicts pass.
                ancestor = target.parent
                while not e.exists(ancestor): ancestor = ancestor.parent
                guard_tree(e, stack, ancestor, seen)
                if e.exists(target):
                    checked_reader(e, stack, target, row); report['skipped'].append(str(target))
                else: missing.append((row, target))
            font_registry_check(rows, reg_read(winreg.HKEY_LOCAL_MACHINE), reg_read(winreg.HKEY_CURRENT_USER))
            report['status'] = 'PREFLIGHT_VERIFIED'; report['plannedAdditions'] = [str(t) for _, t in missing]
            e.atomic_json(receipt, report, replace=False)
            if not apply: return report, receipt
            env_check(True)  # Recheck MAGIX immediately before first target mutation.
            e.space(package, sum(r['bytes'] for r, _ in missing))
            for row, target in missing:
                guard_tree(e, stack, target.parent, seen, create=True, created=report['createdDirectories'])
                create_copy(e, sources[row['sourceRelativePath']], target, row)
                checked_reader(e, stack, target, row)
                report['added'].append(str(target))
                print('Verified addition: ' + row['targetRelativePath'], flush=True)
            register(rows, report)
            report['status'] = 'IMPORTED_VERIFIED'; report['applicationRenderingTested'] = False
        except BaseException as exc:
            report['status'] = 'FAILED_PARTIAL_ADDITIONS_RETAINED' if apply else 'PREFLIGHT_FAILED'
            report['error'] = type(exc).__name__ + ': ' + str(exc)
            failure = package / ('Import-Failure-' + report['runId'] + '.json')
            e.atomic_json(failure, report, replace=False)
            raise RuntimeError(str(exc) + '\nReceipt: ' + str(failure)) from exc
        final = package / ('Import-Result-' + report['runId'] + '.json')
        e.atomic_json(final, report, replace=False)
        return report, final

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    need(SHA.fullmatch(args.manifest_sha256) is not None, 'Invalid manifest SHA256')
    # No user-selected destination/engine path is exposed by the production command.
    engine = load_engine(KIT / 'project_library.py')
    result, path = execute(engine, Path(__file__).absolute().parent, args.manifest_sha256, args.apply)
    print(result['status'] + '\nReceipt: ' + str(path), flush=True)
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True); sys.exit(1)
