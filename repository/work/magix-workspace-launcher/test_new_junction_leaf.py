"""Called only by own ACL fixture; refuses paths outside this test folder."""
from pathlib import Path
import importlib.util
import json
import sys
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('leaf_fixture_workspace',HERE/'magix_workspace.py')
w=importlib.util.module_from_spec(spec);sys.modules[spec.name]=w;spec.loader.exec_module(w)
plan=Path(sys.argv[1]).resolve();assert plan.parent==HERE and plan.name.startswith('leaf-plan-')
data=json.loads(plan.read_text('utf-8-sig'))
parent=Path(data['parent']);target=Path(data['target'])
assert parent.is_relative_to(HERE) and target.is_relative_to(HERE)
assert parent.parts[len(HERE.parts)].startswith('AccessFixture-') and target.parts[len(HERE.parts)].startswith('AccessFixture-')
engine=w.load_engine(HERE.parents[1]/'outputs/Project-Library-2026-10-29/Client/project_library.py')
j=w.Junction(engine,w.current_sid());link=parent/'FreshDocuments'
with engine.directory_guard(parent),engine.directory_guard(target):
    info=j.create(link,target,lambda _:None)
    assert info['target']==w.NT_PREFIX+str(target)
    assert (link/'preserve.txt').read_bytes()==(target/'preserve.txt').read_bytes()
    j.unlink(link,info)
    assert not engine.exists(link)
print('Fresh junction under RX-inheriting parent created/read/unlinked PASS')
