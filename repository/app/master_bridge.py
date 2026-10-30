"""Local stdio MCP bridge for Violet's deliberately reported MASTER status.

Read reduced status, publish a sourced report, and inspect/acknowledge attention.
No shell, project-action, file-path, agent-start or stop capability is exposed.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

STATUSES = ['working', 'waiting', 'turn_finished', 'interrupted', 'unknown']
TOOLS = [
    dict(name='get_master_status', description='Read current project activity, health and temperatures from INTERLINKED MASTER. This does not run or control projects.',
         inputSchema=dict(type='object', properties={}, additionalProperties=False),
         annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
    dict(name='publish_violet_status', description='Publish Violet\'s own short activity update to MASTER, explicitly labelled Dot-reported. A finished turn is not project/test success.',
         inputSchema=dict(type='object', properties=dict(status=dict(type='string', enum=STATUSES),
             message=dict(type='string', maxLength=120), reporter=dict(type='string', enum=['dot', 'local_task', 'demo']), projectId=dict(type='string', pattern='^[A-Za-z0-9_.-]{1,80}$')),
             required=['status'], additionalProperties=False),
         annotations=dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)),
    dict(name='get_master_attention', description='Read queued project failures and requests for input. Reading does not acknowledge them or resolve failures.',
         inputSchema=dict(type='object', properties={}, additionalProperties=False),
         annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
    dict(name='acknowledge_master_attention', description='Explicitly acknowledge retained attention IDs after reviewing them. Does not resolve project failures or control projects.',
         inputSchema=dict(type='object', properties=dict(ids=dict(type='array', minItems=1, maxItems=100, items=dict(type='string', pattern='^[A-Za-z0-9_.-]{1,80}$'))), required=['ids'], additionalProperties=False),
         annotations=dict(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))]


def companion():
    path = Path(__file__).resolve().with_name('interlinked_master.py')
    spec = importlib.util.spec_from_file_location('interlinked_master_bridge_companion', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handle(message, module, root):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise ValueError('Invalid JSON-RPC request.')
    method = message.get('method')
    params = message.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('Invalid request parameters.')
    if method == 'initialize':
        version = params.get('protocolVersion', '2024-11-05')
        if version not in ('2024-11-05', '2025-03-26', '2025-06-18'):
            version = '2024-11-05'
        return dict(protocolVersion=version, capabilities=dict(tools=dict(listChanged=False)),
                    serverInfo=dict(name='interlinked-master-violet', version='1.1.0'))
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return dict(tools=TOOLS)
    if method == 'tools/call':
        name, arguments = params.get('name'), params.get('arguments', {})
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be an object.')
        try:
            if name == 'get_master_status':
                if arguments:
                    raise ValueError('get_master_status takes no arguments.')
                value = module.bridge_snapshot(root)
                stale = not 0 <= time.time() - module.timestamp(value['utc']) <= 15
                value = dict(value, available=not stale, stale=stale)
            elif name == 'publish_violet_status':
                value = module.publish_violet(root, arguments)
            elif name == 'get_master_attention':
                if arguments:
                    raise ValueError('get_master_attention takes no arguments.')
                value = module.master_attention(root)
            elif name == 'acknowledge_master_attention':
                value = module.acknowledge_attention(root, arguments)
            else:
                raise ValueError('Unknown MASTER tool.')
            return dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))], isError=False)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            # Bounded generic errors; do not leak local exception paths/contents.
            return dict(content=[dict(type='text', text='MASTER is unavailable or the requested status is invalid.')], isError=True)
    if method.startswith('notifications/'):
        return None
    raise ValueError('Unsupported MCP method.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path)
    args = parser.parse_args()
    module = companion()
    root = args.state_root or module.common_state_root()
    while True:
        raw = sys.stdin.buffer.readline(module.MAX_JSON + 1)
        if not raw:
            break
        if len(raw) > module.MAX_JSON:
            # Stop rather than interpreting the tail as another request.
            break
        request_id = None
        try:
            request = json.loads(raw)
            request_id = request.get('id') if isinstance(request, dict) else None
            result = handle(request, module, root)
            if request_id is None:
                continue
            response = dict(jsonrpc='2.0', id=request_id, result=result)
        except (ValueError, TypeError, json.JSONDecodeError):
            if request_id is None:
                continue
            response = dict(jsonrpc='2.0', id=request_id, error=dict(code=-32602, message='Invalid MASTER bridge request.'))
        sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
