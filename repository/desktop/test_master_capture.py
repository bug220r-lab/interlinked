"""Exercise real Windows live-mini capture only against our synthetic windows.

The harness loads the separately built worker but never calls its Run/USB paths.
Requires an interactive Windows desktop and a fresh private output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This acceptance check requires interactive Windows')
    worker = args.worker.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise SystemExit('Use a fresh fixture output directory')
    output.mkdir(parents=True)
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    source = Path(__file__).with_name('MasterCaptureFixture.cs')
    executable = output / 'MasterCaptureFixture.exe'
    build = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64',
                            '/optimize+', '/warn:4', '/out:' + str(executable),
                            '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
                            '/reference:System.Web.Extensions.dll', str(source)],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        raise SystemExit(build.stdout + build.stderr)
    result = subprocess.run([str(executable), str(worker), str(output)],
                            capture_output=True, text=True, timeout=35)
    (output / 'fixture-events.jsonl').write_text(result.stderr, encoding='utf-8')
    report_path = output / 'capture-report.json'
    if not report_path.is_file():
        raise SystemExit('Capture fixture did not finish: ' + result.stdout + result.stderr)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['workerSha256'] = hashlib.sha256(worker.read_bytes()).hexdigest()
    report['fixtureSourceSha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    report['count'] = len(report['checks'])
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    if result.returncode or not report['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
