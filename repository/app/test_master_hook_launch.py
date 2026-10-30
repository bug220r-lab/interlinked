"""Real Windows shell launch checks using private, owned hook fixtures only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('These acceptance checks require real Windows PowerShell and CMD.')
    output = args.output.absolute()
    protected = [Path(r'C:\Users\Jude\Documents\MSI-Project-Library'),
                 Path(os.environ.get('LOCALAPPDATA', 'C:/unavailable')) / 'ProjectLibrary']
    if any(output == path or path in output.parents for path in protected):
        parser.error('Use a private fixture folder outside the installed kit and project cache.')
    if output.exists():
        parser.error('Use a fresh absent output directory.')
    output.mkdir(parents=True)
    hook = load('hook_launcher_under_test', ROOT / 'master_hook.py')
    master = load('hook_fixture_metadata', ROOT / 'interlinked_master.py')
    system = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    powershell = system / 'WindowsPowerShell/v1.0/powershell.exe'
    cmd = system / 'cmd.exe'
    checks, timings = [], []

    def check(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)

    def fixture(name):
        folder = output / name
        folder.mkdir()
        project = folder / 'project'
        project.mkdir()
        feed = master.owned_directory(folder / 'feed', 'event-feed')
        master.write_json(feed / 'activity-roots.json',
                          dict(format='interlinked-master-activity-roots-v1', roots=[str(project)]))
        return folder, project, feed

    def run(shell, command, payload):
        argv = ([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command]
                if shell == 'PowerShell' else [str(cmd), '/d', '/s', '/c', command])
        started = time.monotonic()
        result = subprocess.run(argv, input=json.dumps(payload).encode('utf-8'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=output, timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timings.append(dict(shell=shell, seconds=time.monotonic() - started))
        return result

    def exercise(module, name, encoded):
        _, project, feed = fixture(name)
        config = module.hook_config(sys.executable, feed)
        command = config['hooks']['PreToolUse'][0]['hooks'][0]['commandWindows']
        check(('EncodedCommand' in command) == encoded, name + ': expected literal launch strategy')
        check(config['hooks']['PreToolUse'][0]['hooks'][0]['command'] == command,
              name + ': Windows default and override use identical safe launch')
        for shell in ('PowerShell', 'CMD'):
            payload = dict(hook_event_name='UserPromptSubmit', session_id='launch-' + shell,
                           turn_id='turn1', cwd=str(project), event_id=name.replace(' ', '-') + '-' + shell,
                           prompt='SECRET_PROMPT_NOT_METADATA',
                           transcript_path='SECRET_TRANSCRIPT_NOT_METADATA',
                           tool_input={'command': 'SECRET_ARGUMENTS_NOT_METADATA'})
            before = set((feed / 'events').glob('*.json')) if (feed / 'events').exists() else set()
            result = run(shell, command, payload)
            check(result.returncode == 0 and not result.stdout and not result.stderr,
                  name + ': ' + shell + ' invokes observer silently')
            created = set((feed / 'events').glob('*.json')) - before
            check(len(created) == 1, name + ': ' + shell + ' writes exactly one private metadata event')
            event = master.read_json(next(iter(created)))
            check(event['sessionId'] == payload['session_id'] and event['cwd'] == str(project),
                  name + ': ' + shell + ' preserves literal private arguments')
            check('SECRET_' not in json.dumps(event), name + ': ' + shell + ' discards private content')
            payload['cwd'] = str(output / 'unregistered')
            payload['event_id'] += '-outside'
            before = set((feed / 'events').glob('*.json'))
            result = run(shell, command, payload)
            check(result.returncode == 0 and not result.stdout and not result.stderr
                  and set((feed / 'events').glob('*.json')) == before,
                  name + ': ' + shell + ' never observes unregistered projects')
        return project, feed

    try:
        plain_project, plain_feed = exercise(hook, 'plain', False)
        special = output / "special path's & $literal"
        special.mkdir()
        shutil.copyfile(ROOT / 'master_hook.py', special / 'master_hook.py')
        shutil.copyfile(ROOT / 'interlinked_master.py', special / 'interlinked_master.py')
        copied = load('special_path_observer', special / 'master_hook.py')
        exercise(copied, 'special', True)
        special_feed = output / "feed path's & $literal"
        special_command = hook.hook_config(sys.executable, special_feed)['hooks']['Stop'][0]['hooks'][0]['commandWindows']
        check('EncodedCommand' in special_command, 'A special state-root argument is encoded inside the call')
        # The old command must fail under the actual shell before it can emit data.
        old = '"' + sys.executable + '" -I -B "' + str(ROOT / 'master_hook.py') + '" --state-root "' + str(plain_feed) + '"'
        before = set((plain_feed / 'events').glob('*.json'))
        result = run('PowerShell', old, dict(hook_event_name='Stop', session_id='old', cwd=str(plain_project)))
        check(result.returncode != 0 and b'-I' in result.stderr,
              'Negative control reproduces the original PowerShell invocation failure')
        check(set((plain_feed / 'events').glob('*.json')) == before,
              'Old-command negative control creates no metadata event')
        original = dict(description='Keep this description', hooks=dict(PreToolUse=[dict(matcher='existing', hooks=[dict(type='command', command='existing-review-command')])]), customSetting={'preserve': True})
        encoded_original = json.dumps(original, sort_keys=True)
        merged = hook.merged_config(original, sys.executable, plain_feed)
        check(json.dumps(original, sort_keys=True) == encoded_original, 'Merging never mutates the original configuration')
        check(merged['hooks']['PreToolUse'][0] == original['hooks']['PreToolUse'][0]
              and merged['description'] == original['description'] and merged['customSetting'] == original['customSetting'],
              'Merging preserves existing hooks, description and other settings')
        check(hook.merged_config(merged, sys.executable, plain_feed) == merged,
              'Merging the exact generated definitions is idempotent')
        handlers = [group['hooks'][0] for groups in hook.hook_config(sys.executable, plain_feed)['hooks'].values() for group in groups]
        check(len(handlers) == 10 and all(h['timeout'] == 1 for h in handlers), 'All ten definitions retain one-second deadlines')
        check(sum(h.get('async', False) for h in handlers) == 9, 'Only SessionEnd remains synchronous')
        args_common = [sys.executable, '-I', '-B', str(ROOT / 'master_hook.py'), '--python', sys.executable, '--state-root', str(special_feed)]
        result = subprocess.run(args_common + ['--show-config'], capture_output=True, timeout=10)
        check(result.returncode == 0 and json.loads(result.stdout) == hook.hook_config(sys.executable, special_feed),
              '--show-config honors the explicit state root before encoding')
        existing_file, proposal = output / 'existing.json', output / 'proposal.json'
        existing_file.write_text(json.dumps(original), encoding='utf-8')
        result = subprocess.run(args_common + ['--existing-config', str(existing_file), '--proposal', str(proposal)], capture_output=True, timeout=10)
        check(result.returncode == 0 and json.loads(proposal.read_text('utf-8')) == hook.merged_config(original, sys.executable, special_feed),
              '--proposal honors the explicit state root and preserves existing config')
        check(json.loads(existing_file.read_text('utf-8')) == original, '--proposal does not alter its existing-config input')
        report = dict(format='interlinked-master-hook-launch-tests-v1', ok=True, count=len(checks), checks=checks,
                      timings=timings, sourceSha256=hashlib.sha256((ROOT/'master_hook.py').read_bytes()).hexdigest(),
                      productionFeedWritten=False, configChanged=False, trustChanged=False, modelRequests=0)
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(dict(ok=True, count=len(checks), report=str(output/'report.json'), maxShellSeconds=max(t['seconds'] for t in timings))))
        return 0
    except Exception as exc:
        (output/'failure.json').write_text(json.dumps(dict(ok=False, checks=checks, error=str(exc)), indent=2)+'\n', encoding='utf-8')
        raise


if __name__ == '__main__':
    raise SystemExit(main())
