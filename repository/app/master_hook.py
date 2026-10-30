"""Reviewed metadata-only Codex lifecycle observer. Failures never block coding."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def companion():
    # The installed portable interpreter uses -I. Never import from cwd/PYTHONPATH.
    path = Path(__file__).resolve().with_name('interlinked_master.py')
    spec = importlib.util.spec_from_file_location('interlinked_master_hook_companion', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_config(python=None):
    command = '"' + str(python or sys.executable) + '" -I -B "' + str(Path(__file__).resolve()) + '"'
    events = ('SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
              'PermissionRequest', 'Stop', 'Interrupt', 'SubagentStart', 'SubagentStop')
    hooks = {}
    for event in events:
        handler = dict(type='command', command=command, commandWindows=command, timeout=1)
        if event != 'SessionEnd':
            handler['async'] = True
        hooks[event] = [dict(hooks=[handler])]
    return dict(description='INTERLINKED MASTER passive metadata observer; review and trust before use.', hooks=hooks)


def merged_config(existing, python=None):
    """Add only this exact observer; preserve unrelated groups and settings."""
    if not isinstance(existing, dict) or not isinstance(existing.get('hooks', {}), dict):
        raise ValueError('Existing hook configuration is invalid.')
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault('hooks', {})
    for event, additions in hook_config(python)['hooks'].items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise ValueError('Existing hook event is invalid.')
        for group in additions:
            if group not in current:
                current.append(group)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path)
    parser.add_argument('--show-config', action='store_true')
    parser.add_argument('--python')
    parser.add_argument('--existing-config', type=Path)
    parser.add_argument('--proposal', type=Path, help='Write a new merged proposal; never changes the active hook configuration or trust.')
    args = parser.parse_args()
    if args.proposal:
        existing = {}
        if args.existing_config and args.existing_config.exists():
            with args.existing_config.open('r', encoding='utf-8-sig') as stream:
                existing = json.load(stream)
        with args.proposal.open('x', encoding='utf-8') as stream:
            json.dump(merged_config(existing, args.python), stream, indent=2)
        return 0
    if args.show_config:
        print(json.dumps(hook_config(args.python), indent=2))
        return 0
    try:
        module = companion()
        raw = sys.stdin.buffer.read(module.MAX_JSON + 1)
        if len(raw) > module.MAX_JSON:
            return 0
        event = module.sanitize_hook(json.loads(raw))
        root = args.state_root or module.common_state_root()
        # Global hooks observe only explicitly registered projects. Do not spool
        # metadata about unrelated chats or depend on their transcript files.
        registry = module.read_json(root / 'activity-roots.json', 16384)
        if registry.get('format') != 'interlinked-master-activity-roots-v1':
            return 0
        cwd = os.path.normcase(os.path.abspath(event['cwd']))
        roots = [os.path.normcase(os.path.abspath(p)).rstrip('\\/') for p in registry.get('roots', []) if isinstance(p, str) and p]
        if not any(cwd == p or cwd.startswith(p + os.sep) for p in roots):
            return 0
        module.publish_event(root, event)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        # No context, policy decisions, sensitive diagnostics or output to the model.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
