"""Probe only an owned fixture cache lock, without accessing library shares."""
import argparse,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'outputs/Project-Library-2026-10-29/Helper'))
import project_library as lib
parser=argparse.ArgumentParser();parser.add_argument('root');parser.add_argument('--expect',choices=['blocked','available'],required=True);args=parser.parse_args()
root=lib.absolute(args.root);base=Path(r'C:\Users\jlewi\Documents\Codex\MSI-Working-Projects')
lib.need(root.parent==base and root.name.startswith('job-review-20261029-'),'Only this test cache can be probed')
owner=lib.read_json(root/'OWNER.json');lib.need(owner['projectId']==root.name and owner['catalogId']=='msi-project-library-v1','Fixture ownership mismatch')
try:
 with lib.exclusive(root/'.client.lock',create=False):actual='available'
except lib.LibraryError as exc:
 if 'in use' not in str(exc):raise
 actual='blocked'
print(json.dumps(dict(expected=args.expect,actual=actual,cacheRoot=str(root),noNetwork=True)))
sys.exit(actual!=args.expect)
