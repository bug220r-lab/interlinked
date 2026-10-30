"""Exercise the compiled MASTER worker without starting any vendor helper.

Outputs belong in a fresh private fixture folder. This tests real C# rendering,
framing, telemetry and graceful EOF; it does not certify USB hardware or capture.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import queue
import struct
import subprocess
import threading
import time
from pathlib import Path


def jpeg_contract(data):
    """Inspect the actual native JPEG rather than trusting its requested quality."""
    position, geometry, tables = 2, None, []
    while position + 4 < len(data):
        if data[position] != 255:
            raise ValueError('JPEG marker alignment')
        while position < len(data) and data[position] == 255:
            position += 1
        marker = data[position]
        position += 1
        if marker in (0xda, 0xd9):
            break
        if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[position:position+2], 'big')
        if length < 2 or position + length > len(data):
            raise ValueError('JPEG segment bounds')
        segment = data[position+2:position+length]
        if marker == 0xc0:
            geometry = (int.from_bytes(segment[3:5], 'big'), int.from_bytes(segment[1:3], 'big'))
        if marker == 0xdb:
            offset = 0
            while offset < len(segment):
                precision = segment[offset] >> 4
                offset += 1
                if precision not in (0, 1):
                    raise ValueError('JPEG quantization precision')
                width = precision + 1
                if offset + 64*width > len(segment):
                    raise ValueError('JPEG quantization bounds')
                tables.append([int.from_bytes(segment[offset+i*width:offset+(i+1)*width], 'big') for i in range(64)])
                offset += 64*width
        position += length
    return geometry == (480, 480), len(tables) >= 2 and all(value == 1 for table in tables for value in table)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Use a fresh fixture output directory')
    args.output.mkdir(parents=True)
    proc = subprocess.Popen([str(args.worker.resolve())], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    frames, events = queue.Queue(), queue.Queue()
    failures = []

    def read_exact(count):
        result = bytearray()
        while len(result) < count:
            block = proc.stdout.read(count-len(result))
            if not block:
                raise EOFError
            result.extend(block)
        return bytes(result)

    def read_frames():
        try:
            while True:
                count, = struct.unpack('<I', read_exact(4))
                if not 4 < count <= 691136:
                    failures.append('Frame payload length outside contract')
                    return
                data = read_exact(count)
                if not (data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')):
                    failures.append('Invalid encoded JPEG boundaries')
                    return
                frames.put(data)
        except EOFError:
            pass

    def read_events():
        for line in proc.stderr:
            try:
                event = json.loads(line)
                if event.get('type') not in {'stats', 'status', 'capture', 'capture_sources', 'sensors', 'sensor_permission'}:
                    failures.append('Unknown worker event')
                events.put(event)
            except (ValueError, UnicodeError):
                failures.append('Non-JSON worker diagnostic')

    threading.Thread(target=read_frames, daemon=True).start()
    threading.Thread(target=read_events, daemon=True).start()

    def send(value):
        proc.stdin.write((json.dumps(value)+'\n').encode('utf-8'))
        proc.stdin.flush()

    try:
        # These commands cannot elevate or start a publisher while LCD ownership is absent.
        send({'op':'sensors', 'enable':True})
        denied = None
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            try: event=events.get(timeout=.3)
            except queue.Empty: continue
            if event.get('type')=='sensor_permission':
                denied=event
                break
        if not denied or denied.get('status')!='unavailable':
            failures.append('Sensor permission was not refused without LCD ownership')
        send({'op':'sensors', 'enable':False})
        send({'op':'settings', 'settings':{'fps':10, 'diagnostic':True}})
        send({'op':'snapshot', 'snapshot':{'projects':[
            {'id':'edit','label':'ED-IT Buddy','activity':{'status':'working','source':'SYNTHETIC'}},
            {'id':'mw5','label':'MW5 MOD','activity':{'status':'waiting','source':'SYNTHETIC'}},
            {'id':'interlinked','label':'INTERLINKED','activity':{'status':'unknown'}}]}})
        collected = []
        for _ in range(20):
            collected.append(frames.get(timeout=8))
        if len({hashlib.sha256(frame).digest() for frame in collected}) < 12:
            failures.append('Diagnostic sequence did not produce changing frames')
        full_quality = True
        exact_geometry = True
        for frame in collected:
            try:
                geometry, quality = jpeg_contract(frame)
                exact_geometry &= geometry
                full_quality &= quality
            except (ValueError, IndexError):
                exact_geometry = full_quality = False
        if not exact_geometry:
            failures.append('Encoded JPEG is not baseline sequential 480 x 480')
        if not full_quality:
            failures.append('Encoded JPEG does not retain maximum-quality quantization tables')
        (args.output/'frame-first.jpg').write_bytes(collected[0])
        (args.output/'frame-last.jpg').write_bytes(collected[-1])
        # A missing mini source must be explicitly unavailable, never an idle fallback.
        send({'op':'settings','settings':{'fps':5,'mode':'live_mini','captureEnabled':True}})
        capture_event = None
        deadline = time.monotonic()+5
        observed=[]
        while time.monotonic() < deadline:
            try: event=events.get(timeout=.3)
            except queue.Empty: continue
            observed.append(event)
            if event.get('type')=='capture' and event.get('status')!='dashboard':
                capture_event=event
                break
        if not capture_event or capture_event.get('status')!='unavailable':
            failures.append('Missing live-mini source was not reported unavailable')
        # Closing the parent's private input is sufficient to stop the isolated worker.
        proc.stdin.close()
        proc.wait(timeout=7)
        if proc.returncode != 0:
            failures.append('Worker did not exit cleanly on EOF')
        while not events.empty(): observed.append(events.get_nowait())
        if any(e.get('connected') is True for e in observed):
            failures.append('Worker connected LCD without explicit request')
        (args.output/'events.json').write_text(json.dumps(observed,indent=2),encoding='utf-8')
        # Invalid broker CLI data exits before the elevation-only code can do work.
        rejected=0
        for cli in [['--sensor-host'],['--sensor-host','../not-a-pipe'],
                    ['--sensor-host','INTERLINKED-MASTER-SENSOR-'+'0'*32,
                     'a'*64,'b'*64,'c'*64,'d'*64,'-1','1']]:
            result=subprocess.run([str(args.worker.resolve()),*cli],capture_output=True,
                                  timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode==2 and not result.stdout:
                rejected+=1
            else:
                failures.append('Malformed broker arguments did not fail closed')
        report={'ok':not failures,'realEncodedFrames':len(collected),
                'distinctEncodedFrames':len({hashlib.sha256(f).digest() for f in collected}),
                'baselineSequential480Jpeg':exact_geometry,'maximumQualityQuantization':full_quality,
                'missingMiniSourceUnavailable':capture_event is not None,
                'sensorPermissionRefusedWithoutLcd':denied is not None and denied.get('status')=='unavailable',
                'malformedBrokerArgumentsRefused':rejected,
                'gracefulEof':proc.returncode==0,'hardwareTested':False,'errors':failures}
        (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
        if failures: raise SystemExit(1)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


if __name__=='__main__':
    main()
