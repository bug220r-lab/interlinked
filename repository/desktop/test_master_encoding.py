"""Verify UTF-8 worker metadata with an owned Unicode-caption Windows fixture.

The existing capture_sources operation enumerates metadata. This harness never
requests a display, sensor permission, region capture, or another app's UI. It
discards unrelated source metadata and all preview bytes without saving them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


EXPECTED_TITLE = 'Violet — Ω 日本語'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _finish(process):
    """Close only this harness's process through its normal private input."""
    if process is None:
        return None
    graceful = True
    if process.poll() is None:
        try:
            process.stdin.write(b'{"op":"quit"}\n')
            process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=7)
        except (OSError, subprocess.TimeoutExpired):
            graceful = False
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    return dict(exitCode=process.returncode, graceful=graceful and process.returncode == 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This metadata regression requires Windows')
    worker = args.worker.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise SystemExit('Use a fresh private fixture output directory')
    output.mkdir(parents=True)
    source = Path(__file__).with_name('MasterEncodingFixture.cs')
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    executable = output / 'MasterEncodingFixture.exe'
    build = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64',
                            '/optimize+', '/warn:4', '/codepage:65001', '/out:' + str(executable),
                            '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll', str(source)],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        raise SystemExit(build.stdout + build.stderr)
    fixture = probe = None
    errors = []
    own_source = None
    window_id = None
    decoded = False
    report = dict(format='interlinked-master-encoding-test-v1', expectedTitle=EXPECTED_TITLE,
                  workerSha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
                  fixtureSourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  commandsSent=['capture_sources', 'quit'], windowMetadataOnly=True,
                  desktopPixelsCaptured=False, usbRequested=False, sensorsRequested=False,
                  elevationRequested=False)
    try:
        fixture = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(fixture.stdout.readline()), daemon=True).start()
        window_id = str(json.loads(ready.get(timeout=8))['windowId'])
        events = queue.Queue()
        probe = subprocess.Popen([str(worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, creationflags=NO_WINDOW)

        def discard_preview():
            while probe.stdout.read(65536):
                pass

        def metadata():
            for raw in probe.stderr:
                try:
                    value = json.loads(raw.decode('utf-8', 'strict'))
                except (ValueError, UnicodeError) as exc:
                    # No raw caption, other source, or offending byte is retained.
                    events.put(dict(error='Worker diagnostic is not UTF-8 JSON', exceptionType=type(exc).__name__))
                    continue
                if value.get('type') == 'capture_sources':
                    match = next((row for row in value.get('sources', [])
                                  if str(row.get('id')) == window_id), None)
                    # Retain only our fixture's metadata, discarding every other row.
                    events.put(dict(source=match, decoded=True))

        threading.Thread(target=discard_preview, daemon=True).start()
        threading.Thread(target=metadata, daemon=True).start()
        probe.stdin.write(b'{"op":"capture_sources"}\n')
        probe.stdin.flush()
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=.3)
            except queue.Empty:
                continue
            if 'error' in event:
                errors.append(event)
                break
            decoded = event.get('decoded', False)
            own_source = event.get('source')
            break
        if not decoded:
            errors.append('No source event decoded as strict UTF-8 JSON')
        if own_source is None:
            errors.append('Owned Unicode-caption source was not returned')
        elif own_source.get('title') != EXPECTED_TITLE:
            errors.append('Owned Unicode caption did not round-trip exactly')
    except (OSError, ValueError, KeyError, queue.Empty, subprocess.TimeoutExpired) as exc:
        errors.append(dict(error='Owned encoding fixture failed', exceptionType=type(exc).__name__))
    finally:
        report['workerShutdown'] = _finish(probe)
        report['fixtureShutdown'] = _finish(fixture)
    for key in ('workerShutdown', 'fixtureShutdown'):
        if not report[key] or not report[key]['graceful']:
            errors.append(key + ' was not graceful')
    report.update(ok=not errors, strictUtf8JsonDecoded=decoded, ownedWindowId=window_id,
                  ownedSource=own_source, errors=errors)
    (output / 'encoding-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(dict(ok=report['ok'], strictUtf8JsonDecoded=decoded,
                         exactOwnedCaption=own_source is not None and own_source.get('title') == EXPECTED_TITLE,
                         workerShutdown=report['workerShutdown'], fixtureShutdown=report['fixtureShutdown'], errors=errors)))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
