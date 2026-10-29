"""Bounded added tests reuse only our prior synthetic fixture, never real media."""
from pathlib import Path
import importlib.util
import json
import sys

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('launch_candidate',HERE/'magix_projects.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
b=m.load_base(HERE.parents[1]/'magix-bootstrap-fix-2026-10-29/magix_workspace.py')
engine=HERE.parents[2]/'outputs/Project-Library-2026-10-29/Client/project_library.py'
e=b.load_engine(engine)
receipt=json.loads((HERE/'test-results.json').read_text())
root=Path(receipt['fixtureRoot'])
assert root.parent==HERE/'fixtures'
catalog=e.read_json(root/'library/catalog.json')
index=dict(projects=[dict(id=p['id']) for p in catalog['projects']])
c=b.Config(root/'kit',root/'cache',root/'legacy',str(root/'library'),str(root/'returns'),sid=b.current_sid(),test_local=True,client_path=engine,python_path=Path(sys.executable))
a=b.Workspace(c,e,processes=lambda:[]);manager=m.Projects(b,a,index)
checks=[]
def check(value,label):
    assert value,label
    checks.append(label)
def rejects(fn,label):
    try:fn()
    except Exception:checks.append(label)
    else:raise AssertionError(label)
manager.activate()
A=b.PROJECT;B='magix-fixture-b';selected=manager.selected(A)
entry=m.PREFIX+'OneDrive/Documents/shared project.MVP'
physical=selected.c.project_root/'Files'/entry
with manager.operation(),e.exclusive(selected.c.project_root/'.client.lock'):
    manager.bind(selected)
    alias=manager.launch_path(A,physical)
    check(alias==manager.link('OneDrive/Documents')/'shared project.MVP' and alias.read_bytes()==physical.read_bytes(),'Native alias launch addresses exact selected physical file')
    rejects(lambda:manager.launch_path(B,manager.selected(B).c.project_root/'Files'/entry),'Different selected project cannot reuse active aliases')
manager.recover()
rejects(lambda:manager.launch_path(A,physical),'Unbound aliases cannot launch')

# Target mutation does not prevent exact cleanup of the outer owned alias.
with manager.operation(),e.exclusive(selected.c.project_root/'.client.lock'):manager.bind(selected)
target=manager.target(A,'OneDrive/Documents');saved=target.with_name('Documents-fixture-preserved')
target.rename(saved)
inner=manager.j.create(target,manager.target(B,'OneDrive/Documents'),lambda _:None)
try:
    rejects(lambda:manager.launch_path(A,physical),'Reparse target blocks launch before any app call')
    manager.recover()
    check(not e.exists(manager.link('OneDrive/Documents')),'Exact outer alias cleanup works when its target became unsafe')
    check(manager.j.info(target)==inner,'Recovery leaves changed inner target untouched')
finally:
    manager.j.unlink(target,inner);saved.rename(target)

shipped=m.load_index(HERE/'MAGIX-All-Projects-Index.json',e)
count=0;max_length=0
for project in shipped['projects']:
    for entrypoint in project['entryPoints']:
        mapping=m.alias_relative(entrypoint['path'])
        assert mapping and mapping[0] in project['aliasRoots']
        path=Path(r'C:\Users\jlewi')/mapping[0]/mapping[1]
        assert len(str(path))<=259
        max_length=max(max_length,len(str(path)));count+=1
check(count==276,'All 276 indexed entries derive a supported short local alias')
result=dict(status='PASS',checks=checks,count=len(checks),entrypoints=count,maxProductionAliasCharacters=max_length,engineSha256=e.file_digest(HERE/'magix_projects.py'),fixtureRoot=str(root),noNetwork=True,noApplicationLaunch=True)
(HERE/'launch-path-results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
