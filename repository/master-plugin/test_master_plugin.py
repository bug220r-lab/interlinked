"""Validate the private local plugin and read MASTER; never publish as Violet.

Builds only into a fresh workspace fixture. Installed files, plugin settings,
hook trust and the live Violet status are never changed. Run with the normal
Windows user environment; restricted runners can redirect LOCALAPPDATA.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


HERE = Path(__file__).resolve().parent
KIT = Path(r'C:\Users\Jude\Documents\MSI-Project-Library')
STATE = Path(r'C:\Users\Jude\AppData\Local\INTERLINKED\MASTER-status')
RUNTIME = KIT / 'Project-Library-Client/Runtime/python.exe'
BRIDGE = KIT / 'INTERLINKED-App/master_bridge.py'
ARGS = ['-I', '-B', str(BRIDGE), '--state-root', str(STATE)]
MEMBERS = {'.codex-plugin/plugin.json', '.mcp.json', 'skills/master-status/SKILL.md'}
CLIENT_SEAL = '362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ordinary(path):
    path = Path(path).absolute()
    for ancestor in [*reversed(path.parents), path]:
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked plugin paths are unsupported.')
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Plugin metadata must have one file link.')
    return path


def read_json(path):
    path = ordinary(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError('Plugin metadata exceeds bounds.')
    value = json.loads(path.read_text('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Plugin metadata must be an object.')
    return value


def validate(manifest, mcp):
    if manifest.get('name') != 'interlinked-master' or manifest.get('version') != '1.1.1':
        raise ValueError('Unexpected plugin identity.')
    if manifest.get('requires_local_executor') is not True:
        raise ValueError('The plugin must require local execution.')
    if manifest.get('skills') != './skills/' or manifest.get('mcpServers') != './.mcp.json':
        raise ValueError('Unexpected package component references.')
    if set(mcp) != {'mcpServers'} or set(mcp['mcpServers']) != {'interlinked_master'}:
        raise ValueError('The package must expose only its MASTER stdio server.')
    server = mcp['mcpServers']['interlinked_master']
    if not isinstance(server, dict) or set(server) != {'command', 'args'}:
        raise ValueError('Unexpected MCP launch capabilities.')
    if server['command'] != str(RUNTIME) or server['args'] != ARGS:
        raise ValueError('MCP must use the reviewed installed runtime, bridge and fixed state root.')
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path, help='Validate an existing prepared package instead of building one.')
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists():
        raise SystemExit('Use a new private test output directory.')
    output.mkdir(parents=True)
    checks = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    package = args.package.absolute() if args.package else output / 'package'
    if args.package is None:
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(HERE / 'Build-MasterPlugin.ps1'), '-OutputDirectory', str(package)]
        built = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, creationflags=flags)
        check(built.returncode == 0, 'Fresh private package builds without modifying an installed plugin')
        before = digest(package / '.mcp.json')
        duplicate = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, creationflags=flags)
        check(duplicate.returncode != 0 and digest(package / '.mcp.json') == before,
              'Builder refuses and preserves a nonempty package')

    ordinary(package)
    members = {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    check(members == MEMBERS, 'Package contains exactly the compatibility manifest, skill and private stdio wiring')
    for rel in MEMBERS:
        ordinary(package / rel)
    manifest = read_json(package / '.codex-plugin/plugin.json')
    mcp = read_json(package / '.mcp.json')
    server = validate(manifest, mcp)
    check(True, 'Manifest and MCP wiring use the installed isolated runtime and explicit owned state root')
    check((package / '.codex-plugin/plugin.json').read_bytes() == (HERE / '.codex-plugin/plugin.json').read_bytes(),
          'Prepared compatibility manifest matches reviewed source')
    check((package / 'skills/master-status/SKILL.md').read_bytes() == (HERE / 'skills/master-status/SKILL.md').read_bytes(),
          'Prepared skill matches reviewed source')

    for label, mutate in [
        ('arbitrary runtime', lambda v:v['mcpServers']['interlinked_master'].update(command='cmd.exe')),
        ('uncontrolled environment', lambda v:v['mcpServers']['interlinked_master'].update(env={'PATH':'unreviewed'})),
        ('missing explicit state root', lambda v:v['mcpServers']['interlinked_master'].update(args=ARGS[:3])),
        ('source checkout bridge', lambda v:v['mcpServers']['interlinked_master'].update(args=['-I','-B',str(HERE.parent/'app/master_bridge.py'),'--state-root',str(STATE)])),
        ('remote MCP transport', lambda v:v['mcpServers']['interlinked_master'].update(url='https://example.invalid/mcp')),
    ]:
        bad = deepcopy(mcp); mutate(bad)
        try:
            validate(manifest, bad)
        except ValueError:
            check(True, 'Configuration validation rejects ' + label + ' before execution')
        else:
            raise AssertionError('Unsafe configuration accepted: ' + label)

    seal = read_json(KIT / 'INTERLINKED-App/GUI-CHECKSUMS.json')
    expected_gui = {'interlinked_gui.py','interlinked_controller.py','interlinked_hub.py','interlinked_mw5.py',
                    'interlinked_master.py','master_hook.py','master_bridge.py','interlinked_jobs.py','interlinked_buddy.py',
                    'web/index.html','web/app.js','web/style.css'}
    check(seal.get('format') == 'interlinked-gui-checksums-v5' and set(seal.get('files', {})) == expected_gui,
          'Installed application uses the current twelve-file Buddy-compatible GUI seal')
    check(all(seal['files'].get(name) == digest(ordinary(KIT / 'INTERLINKED-App' / name))
              for name in ('master_bridge.py', 'interlinked_master.py', 'master_hook.py')),
          'Installed bridge and companions match the application seal')
    client_path = KIT / 'Project-Library-Client/HELPER-CHECKSUMS.json'
    check(digest(ordinary(client_path)) == CLIENT_SEAL, 'Portable runtime dependency seal matches its preserved pin')
    client = read_json(client_path)
    runtime_row = next((r for r in client.get('files', []) if r.get('path') == 'Runtime/python.exe'), None)
    check(runtime_row is not None and digest(ordinary(RUNTIME)) == runtime_row['sha256'],
          'Configured portable Python matches its reviewed sealed bytes')

    violet = STATE / 'violet.json'
    violet_before = digest(violet) if violet.exists() else None
    requests = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion':'2025-06-18'}),
                dict(jsonrpc='2.0', method='notifications/initialized'),
                dict(jsonrpc='2.0', id=2, method='tools/list'),
                dict(jsonrpc='2.0', id=3, method='tools/call', params={'name':'get_master_status','arguments':{}})]
    process = subprocess.run([server['command'], *server['args']],
        input=''.join(json.dumps(r)+'\n' for r in requests).encode('utf-8'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, creationflags=flags)
    check(process.returncode == 0 and not process.stderr, 'Installed stdio server initializes and exits cleanly')
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    check([r.get('id') for r in replies] == [1,2,3], 'MCP replies correlate correctly and notifications have no response')
    tools = replies[1]['result']['tools']
    check([r['name'] for r in tools] == ['get_master_status','publish_violet_status','get_master_attention','acknowledge_master_attention'], 'Installed bridge exposes exactly its four intended tools')
    check([tool['annotations']['readOnlyHint'] for tool in tools] == [True,False,True,False],
          'Reading, publishing and acknowledgment have distinct mutation annotations')
    result = replies[2]['result']
    check(result.get('isError') is False, 'Read-only live MASTER status request succeeds')
    snapshot = json.loads(result['content'][0]['text'])
    check(snapshot.get('format') == 'interlinked-master-snapshot-v1' and snapshot.get('version') == 1,
          'Live snapshot uses the versioned MASTER schema')
    check(snapshot.get('available') is True and snapshot.get('stale') is False and snapshot.get('demo') is False,
          'Read result is fresh production telemetry')
    check(not {'settings','captureSources','output','token'} & set(snapshot), 'Reduced snapshot excludes private controls and raw operation output')
    check(violet_before == (digest(violet) if violet.exists() else None), 'Read-only smoke does not create or alter Violet reported status')
    report = dict(format='interlinked-master-plugin-tests-v1', ok=True, count=len(checks), checks=checks,
        package=str(package), snapshotUtc=snapshot['utc'], readOnlyLiveSmoke=True,
        publishedVioletStatus=False, actualDotVerified=False, activeConfigurationModified=False,
        hookTrustModified=False, pluginActivated=False, completedUtc=datetime.now(timezone.utc).isoformat())
    (output / 'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('ok','count','readOnlyLiveSmoke','actualDotVerified')} | {'report':str(output/'report.json')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
