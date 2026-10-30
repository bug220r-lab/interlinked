"""Real-process desktop handshake checks using synthetic projects only.

No installed application, MAGIX project, or production state is modified.
The explicit output directory retains private fixture evidence outside Git.
"""
from pathlib import Path
import argparse
import http.client
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve() / ('fixture-desktop-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    state = root / 'state'
    app = Path(__file__).resolve().with_name('interlinked_gui.py')
    # This traps any accidental browser launch, even if the browser could not
    # be observed in an isolated test environment. --no-browser is omitted.
    bootstrap = (
        'import runpy,sys,webbrowser\n'
        'def browser_forbidden(*a,**k):\n'
        ' raise RuntimeError("Desktop mode attempted browser launch")\n'
        'webbrowser.open=browser_forbidden\n'
        'sys.argv=sys.argv[1:]\n'
        'runpy.run_path(sys.argv[0],run_name="__main__")\n'
    )
    command = [sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap,
               str(app), '--desktop', '--demo', '--demo-state', str(state)]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', creationflags=flags)
    checks = []
    origin = token = None

    def check(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)

    def request(path, body=None, *, supplied_token=None, supplied_origin=None):
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        headers = {'X-INTERLINKED-Token': token if supplied_token is None else supplied_token}
        if body is not None:
            headers.update({'Content-Type': 'application/json',
                            'Origin': origin if supplied_origin is None else supplied_origin})
        try:
            connection.request('GET' if body is None else 'POST', path,
                               None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def until(predicate, label):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        raise AssertionError(label)

    try:
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=15)
        except queue.Empty:
            raise AssertionError('Desktop startup handshake timed out')
        if not line:
            raise AssertionError('Desktop startup failed: ' + process.stderr.read(3000))
        handshake = json.loads(line)
        check(set(handshake) == {'format', 'url'}, 'handshake exposes only version and local launch URL')
        check(handshake['format'] == 'interlinked-desktop-startup-v1', 'desktop handshake format')
        parsed = urlsplit(handshake['url'])
        origin = 'http://127.0.0.1:' + str(parsed.port)
        token = parsed.fragment
        check(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and
              parsed.path == '/' and not parsed.query and len(token) >= 32,
              'ephemeral loopback URL carries private fragment token')
        status, current = request('/api/state')
        check(status == 200 and current['demo'] and current['canQuit'],
              'desktop mode serves authenticated demo with browser launch trapped')
        check(request('/api/state', supplied_token='invalid')[0] == 403,
              'invalid capability token refused')
        check(request('/api/quit', {}, supplied_origin='https://foreign.invalid')[0] == 409,
              'foreign write origin refused')
        startups = list(state.glob('run-*/startup.json'))
        check(len(startups) == 1, 'one startup evidence record')
        evidence = startups[0].read_text('utf-8-sig')
        check(token not in evidence and handshake['url'] not in evidence and
              json.loads(evidence)['tokenRecorded'] is False,
              'startup evidence omits capability token')
        duplicate = subprocess.run(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', timeout=15,
                                   creationflags=flags)
        check(duplicate.returncode == 2 and 'already running' in duplicate.stderr,
              'second service using same owned state refused')
        check(process.poll() is None and request('/api/state')[0] == 200,
              'duplicate attempt preserves original service')
        status, catalog = request('/api/catalog')
        project = catalog['projects'][0]
        check(status == 200, 'demo catalog available')
        entry = project['entryPoints'][0]['path']
        status, job = request('/api/action', {'action': 'open', 'project': project['id'], 'entry': entry})
        check(status == 200, 'synthetic open accepted')
        check(request('/api/quit', {})[0] == 409, 'close refused during synthetic operation')
        active = until(lambda: (lambda s: s if s['operation']['awaitingDone'] else None)(
            request('/api/state')[1]), 'synthetic session prompt did not appear')
        check(request('/api/quit', {})[0] == 409, 'close refused while MAGIX confirmation pending')
        check(process.poll() is None, 'rejected close leaves service alive')
        active_job = active['operation']
        status, _ = request('/api/session', {'response': 'DONE', 'operationId': active_job['id'],
                                            'promptGeneration': active_job['promptGeneration']})
        check(status == 200, 'explicit Done accepted')
        finished = until(lambda: (lambda s: s if s['canQuit'] else None)(request('/api/state')[1]),
                         'synthetic session did not finish')
        check(finished['operation']['status'] == 'DEMO_COMPLETE', 'Done completes demo without save or release')
        check(request('/api/quit', {})[0] == 200, 'idle graceful exit accepted')
        check(process.wait(timeout=10) == 0, 'service exits cleanly after guarded quit')
        remainder = process.stdout.read()
        check(token not in remainder, 'launch token not repeated in service output')
        check(not process.stderr.read(), 'desktop demo exits without error output')
        receipt = dict(status='PASS', scope='Real-process synthetic desktop startup; no MAGIX or production changes',
                       checks=checks, count=len(checks))
        result = root / 'desktop-startup-results.json'
        result.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(dict(status='PASS', count=len(checks), receipt=str(result))))
    finally:
        if process.poll() is None:
            # Only this uniquely owned synthetic child may be stopped here.
            # Production shutdown is exercised through its guarded API above.
            process.terminate()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


if __name__ == '__main__':
    main()
