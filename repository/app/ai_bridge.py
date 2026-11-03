"""INTERLINKED AI stdio MCP facade. Connects to one running coordinator only."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

MAX_JSON = 2 * 1024 * 1024
ID = dict(type='string', pattern='^[a-f0-9]{32}$')
HOST = dict(type='string', enum=['local', 'msi'])
MODEL = dict(type='string', minLength=1, maxLength=256)
PROJECT = dict(type='string', minLength=1, maxLength=200)
SCOPE = dict(type='string', enum=['approved_projects', 'selected_projects', 'supplied_text_only'])
CONTEXT = dict(contextScope=SCOPE, projectIds=dict(type='array', maxItems=32, uniqueItems=True, items=PROJECT))
ROUTING = dict(host=HOST, model=MODEL, routingMode=dict(type='string', enum=['balanced','fast','best_quality']))
SETTINGS = dict(type='object', additionalProperties=False,
    properties=dict(disabledModels=dict(type='array', maxItems=256, items=dict(type='string', maxLength=300)),
        contextTokens=dict(type='integer', minimum=2048, maximum=32768), responseTokens=dict(type='integer', minimum=128, maximum=8192),
        requestSeconds=dict(type='integer', minimum=15, maximum=1800), queueSeconds=dict(type='integer', minimum=1, maximum=3600),
        speakReplies=dict(type='boolean')),
    required=['disabledModels','contextTokens','responseTokens','requestSeconds','queueSeconds','speakReplies'])


def tool(name, description, properties=None, required=(), readonly=False):
    return dict(name=name, description=description,
        inputSchema=dict(type='object', properties=properties or {}, required=list(required), additionalProperties=False),
        annotations=dict(readOnlyHint=readonly, destructiveHint=False, idempotentHint=True, openWorldHint=False))


TOOLS = [
    tool('get_ai_state', 'Read reduced cached model/resource/calibration/settings observations and approved project/check-profile IDs. Does not refresh or run inference.', readonly=True),
    tool('search_context', 'Search bounded excerpts from already-approved projects. Default scope is all approved projects. Never approves or indexes roots.',
         dict(query=dict(type='string', minLength=1, maxLength=1000), **CONTEXT), ['query'], True),
    tool('submit_analysis', 'Submit bounded text analysis within the assigned task. Returns a durable job ID. Default context includes approved projects; supplied_text_only disables project retrieval.',
         dict(requestId=ID, text=dict(type='string', minLength=1, maxLength=24000), **CONTEXT, **ROUTING), ['requestId','text']),
    tool('classify_prompt', 'Evaluate the fixed local prompt classifier on supplied text only. No context retrieval or task execution. Returns a durable job ID; qualification requires separate grading.',
         dict(requestId=ID, text=dict(type='string', minLength=1, maxLength=24000), host=HOST, model=MODEL), ['requestId','text','host','model']),
    tool('start_worker', 'Assign an isolated worker to one approved project with bounded permissions, reviewed checks and budget. Retrieve its diff for user review; this tool cannot apply it.',
         dict(requestId=ID, projectId=PROJECT, prompt=dict(type='string', minLength=1, maxLength=32000),
              permissions=dict(type='array', minItems=1, maxItems=3, uniqueItems=True, items=dict(type='string', enum=['read','write','check'])),
              checkProfiles=dict(type='array', maxItems=32, uniqueItems=True, items=dict(type='string', pattern='^[a-z0-9-]{1,60}$')),
              maxSteps=dict(type='integer', minimum=1, maximum=30), timeoutSeconds=dict(type='integer', minimum=10, maximum=3600),
              executionHost=dict(type='string', enum=['auto','local','msi']), **CONTEXT, **ROUTING), ['requestId','projectId','prompt']),
    tool('refresh_models', 'Explicitly refresh model/resource observations using the existing coordinator. Returns a durable job ID; does not start calibration or download models.', dict(requestId=ID), ['requestId']),
    tool('calibrate_model', 'Run bounded capability trials as part of the assigned task. Image/audio trials require a locally approved fixture ID. Returns a cancellable job ID; results prove only the recorded trials.',
         dict(requestId=ID, host=HOST, model=MODEL, capability=dict(type='string', enum=['text','coding','tools','vision','audio_speech','audio_general']), fixtureId=ID), ['requestId','host','model','capability']),
    tool('load_model', 'Request loading an already installed local model within resource limits. Does not install or upgrade models. Returns a durable job ID.', dict(requestId=ID,host=HOST,model=MODEL), ['requestId','host','model']),
    tool('request_unload', 'Request unloading an INTERLINKED-owned model. Actual unloading requires single-use confirmation in the local UI that other clients are idle. Never invent that confirmation.', dict(requestId=ID,host=HOST,model=MODEL), ['requestId','host','model']),
    tool('update_ai_settings', 'Change persistent AI settings only when the human explicitly requests it. Supply the observed settings revision; retries must reuse the original request ID.',
         dict(requestId=ID, settingsRevision=dict(type='string', minLength=1, maxLength=128), value=SETTINGS), ['requestId','settingsRevision','value']),
    tool('get_job', 'Read status and provenance for a bridge-created job. Polling does not submit or repeat work.', dict(id=ID), ['id'], True),
    tool('get_result', 'Read bounded results and evidence for a bridge-created job. Model completion is not test success; result application remains a user action.', dict(id=ID), ['id'], True),
    tool('cancel_job', 'Request cancellation of this bridge-created job, retaining partial evidence. Does not terminate the shared model server or unrelated jobs.', dict(id=ID), ['id']),
]
SCHEMAS = {item['name']: item['inputSchema'] for item in TOOLS}
INSTRUCTIONS = ('Use INTERLINKED AI only within a user-assigned task. Reuse request IDs for retries. '
    'Default context searches already-approved projects; select supplied_text_only when appropriate. '
    'Permanent settings require an explicit human request. Unload requires actual local UI confirmation. '
    'Retrieve and review results; never claim model completion proves checks passed. '
    'The tools cannot approve roots, register commands, apply results, pair hosts or download models.')


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) - set(schema['properties']) or set(schema.get('required', [])) - set(value):
            raise ValueError('Invalid arguments.')
        for key, item in value.items(): validate(item, schema['properties'][key])
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 256):
            raise ValueError('Invalid array.')
        for item in value: validate(item, schema['items'])
        if schema.get('uniqueItems') and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError('Duplicate array entries.')
    elif kind == 'string':
        if not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 32000) or '\0' in value:
            raise ValueError('Invalid text.')
        if 'pattern' in schema and re.fullmatch(schema['pattern'], value) is None: raise ValueError('Invalid identity.')
    elif kind == 'integer':
        if type(value) is not int or not schema.get('minimum', 0) <= value <= schema.get('maximum', 2**31): raise ValueError('Invalid integer.')
    elif kind == 'boolean':
        if type(value) is not bool: raise ValueError('Invalid boolean.')
    else:
        raise ValueError('Unsupported schema.')
    if 'enum' in schema and value not in schema['enum']: raise ValueError('Invalid choice.')


def companion():
    path = Path(__file__).absolute().with_name('interlinked_ai_ipc.py')
    spec = importlib.util.spec_from_file_location('interlinked_ai_ipc_bridge', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_id(value):
    return type(value) is int and -(2**63) <= value < 2**63 or isinstance(value, str) and 0 < len(value) <= 128


def handle(message, ipc, endpoint):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0': raise ValueError('Invalid JSON-RPC.')
    method, params = message.get('method'), message.get('params', {})
    if not isinstance(method, str) or not isinstance(params, dict): raise ValueError('Invalid request.')
    if method.startswith('notifications/'): return None
    if not valid_id(message.get('id')): raise ValueError('A correlated request ID is required.')
    if method == 'initialize':
        version = params.get('protocolVersion', '2025-06-18')
        if version not in ('2024-11-05','2025-03-26','2025-06-18'): version = '2025-06-18'
        return dict(protocolVersion=version, capabilities=dict(tools=dict(listChanged=False)),
                    serverInfo=dict(name='interlinked-ai',version='1.0.0'), instructions=INSTRUCTIONS)
    if method == 'ping': return {}
    if method == 'tools/list': return dict(tools=TOOLS)
    if method != 'tools/call': raise ValueError('Unsupported method.')
    name, body = params.get('name'), params.get('arguments', {})
    try:
        if name not in SCHEMAS: raise ValueError('Unknown tool.')
        validate(body, SCHEMAS[name])
        value = ipc.request(endpoint, name, body)
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(text.encode('utf-8')) > MAX_JSON - 4096: raise ValueError('Oversized result.')
        return dict(content=[dict(type='text', text=text)], isError=False)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return dict(content=[dict(type='text', text=json.dumps(dict(error='coordinator_unavailable_or_request_refused',
            message='Open the reviewed INTERLINKED installation and check the request. No work was automatically retried.')))], isError=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=Path, required=True)
    args = parser.parse_args()
    ipc = companion()
    while True:
        raw = sys.stdin.buffer.readline(MAX_JSON + 1)
        if not raw or len(raw) > MAX_JSON: break
        request_id = None
        try:
            message = ipc.decode(raw)
            request_id = message.get('id') if isinstance(message, dict) else None
            if not valid_id(request_id): request_id = None
            result = handle(message, ipc, args.endpoint)
            if request_id is None: continue
            response = dict(jsonrpc='2.0', id=request_id, result=result)
        except (ValueError, RuntimeError, TypeError):
            if request_id is None: continue
            response = dict(jsonrpc='2.0', id=request_id, error=dict(code=-32602, message='Invalid INTERLINKED AI request.'))
        sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__': raise SystemExit(main())
