"""Exercise the shipped portable runtime and engine against a local fixture."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

here=Path(__file__).resolve().parent
client=here.parents[1]/'outputs/Project-Library-2026-10-29/Client'
engine=client/'project_library.py';python=client/'Runtime/python.exe'
results=[]
with tempfile.TemporaryDirectory(prefix='portable-smoke-',dir=here) as tmp:
    base=Path(tmp);library=base/'library';returns=base/'returns';cache=base/'cache'
    library.mkdir();returns.mkdir();(library/'.library-access.lock').write_bytes(b'fixture')
    source=library/'Sources/Drive_C/project/fixture.txt';source.parent.mkdir(parents=True);source.write_bytes(b'portable runtime fixture')
    s=source.stat()
    catalog={'format':'project-library-catalog-v1','catalogId':'portable-fixture','projects':[{'id':'sample','name':'Fixture','description':'Local synthetic only','files':[{'path':'Drive_C/project/fixture.txt','sourceRelative':'Sources/Drive_C/project/fixture.txt','bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'kind':'file'}],'notes':[],'warnings':[]}]}
    (library/'catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
    for command in [('list',),('fetch','sample'),('save','sample'),('verify','sample'),('release','sample','--confirm','sample')]:
        result=subprocess.run([str(python),'-B','-X','utf8',str(engine),'--library',str(library),'--returns',str(returns),'--cache',str(cache),'--test-local',*command],capture_output=True,text=True,encoding='utf-8')
        output=json.loads(result.stdout);assert result.returncode==0,(command,result.returncode,output,result.stderr)
        results.append({'command':command[0],'status':output.get('status','LISTED'),'exitCode':result.returncode})
    assert source.read_bytes()==b'portable runtime fixture'
    assert not (cache/'sample/Files/Drive_C/project/fixture.txt').exists()
report={'engineSha256':hashlib.sha256(engine.read_bytes()).hexdigest(),'passed':True,'operations':results,'scope':'Shipped runtime with local fixtures only; no production share access'}
(here/'client-runtime-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
