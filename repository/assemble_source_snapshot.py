"""Assemble the reviewed text-source snapshot; no Git, network or app actions.

Historical packaging utility. Copies only the explicit allowlist below and
refuses every existing destination. It never enumerates source payload trees.
"""
from __future__ import annotations
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OLD = Path(r'C:\Users\jlewi\Documents\Codex\2026-10-28\prep')
CURRENT = Path(r'C:\Users\jlewi\OneDrive\Documents\ChatGPT\INTERLINKED')
DEST = CURRENT / 'repository'
PACKAGE = 'outputs/Project-Library-2026-10-29/'
ALLOWED = {'.py', '.ps1', '.cmd', '.txt', '.json', '._pth', '.md'}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    chosen = []
    def add(relative, role, *, source=None, target=None):
        chosen.append((source or OLD / relative, target or relative, role))

    for name in ['project_library.py', 'publish_library.py', 'Set-Up-MSI-Sharing.cmd', 'Set-Up-MSI-Sharing.ps1']:
        add(PACKAGE+'Helper/'+name, 'host publication and SMB setup source')
    for name in ['project_library.py', 'Project-Library.ps1', 'Project-Library.cmd', 'Open-ProjectConsole.ps1', 'README.txt', 'START-HERE.txt', 'HELPER-CHECKSUMS.json', 'Runtime/LICENSE.txt', 'Runtime/python312._pth']:
        add(PACKAGE+'Client/'+name, 'exact sealed client text or checksum dependency')
    for name in ['Check-MSI-Connection.cmd', 'Check-MSI-Connection.ps1', 'Repair-Direct-PC-Names.cmd', 'Repair-Direct-PC-Names.ps1', 'Sign-In-To-MSI.cmd', 'Open-MSI-Terminal.cmd']:
        add(PACKAGE+name, 'connection helper source')
    for name in ['Enable-Trusted-Direct-Ethernet.ps1', 'Invoke-MSIJob.ps1', 'JOB-SPEC-EXAMPLE.json', 'Launch-Coding-Server-Setup.ps1', 'msi_job_process.py', 'msi_job_runner.py', 'Repair-Coding-Server-Firewall.ps1', 'Repair-Trusted-Direct-Ethernet.ps1', 'Restore-Trusted-Direct-Ethernet-Now.ps1', 'Set-Up-Coding-Server.ps1', 'Set-Up-Direct-Ethernet-Repair.ps1', 'Start-MSIJob.ps1', 'Verify-Coding-Server.ps1']:
        add(PACKAGE+'Server-Setup/'+name, 'guarded host setup or background-job source')
    for name in ['build_ethernet_manifest.py', 'prepare_magix_selection.py', 'prepare_magix_capture_selection.py']:
        add('work/'+name, 'historical selection builder; local inputs excluded')
    for name in ['capture_export.py', 'restore_export.py']:
        add('work/full-export-tools/'+name, 'selection builder import dependency and original archive tooling')
    add('work/project-library-catalog/build_catalog.py', 'catalog builder; local inputs excluded')
    groups = {
        'project-library-tests': ['test_project_library.py', 'test_name_repair.ps1', 'check_connection_checker.ps1', 'check_client_package.ps1', 'client_runtime_smoke.py', 'build_project_index.py'],
        'project-library-sharing': ['test_client_independent.py', 'test_publisher.py', 'test_sharing_helper.ps1'],
        'msi-server-conversion': ['test_job_process.py', 'test_job_unc_identity.py', 'test_scheduled_job.ps1', 'test_direct_ethernet_repair.ps1', 'test_installed_direct_ethernet_task.ps1', 'test-ssh-firewall-repair.ps1', 'test-ssh-resume-guards.ps1', 'test-ssh-retry-native-guards.ps1', 'Prepare-SSH-Job-Fixture.ps1', 'Read-Verified-SSH-Public-Key.ps1', 'probe_job_cache_lock.py'],
        'magix-workspace-launcher': ['MAGIX-Workspace-Access.ps1', 'Set-Up-MAGIX-Workspace.cmd', 'MAGIX-Local-Workspace.cmd', 'test_workspace.py', 'test_access_native.ps1', 'test_access_plan.ps1', 'test_new_junction_leaf.py', 'independent_process_guard_check.py'],
        'magix-legacy-path-option': ['MAGIX-Legacy-Path.ps1', 'test_local_option.ps1'],
        'ssh-job-verification': ['Connect-To-MSI.ps1', 'Invoke-SSH-Job-Verification.ps1', 'Open-MSI-Terminal.cmd', 'SSH-Verification-Common.ps1', 'Verify-MSI-SSH-Job.cmd', 'prepare_plan.py', 'prepare_null_trigger_retest.py', 'test_preparation.ps1', 'test-null-trigger-repair.ps1'],
        'new-pc-workflow': ['prepare_fixture.py', 'Publish-Reviewed-Fixture.ps1', 'test_workflow.py', 'verify_new_pc_workflow.py', 'Verify-New-PC-Workflow.cmd'],
    }
    for folder, names in groups.items():
        for name in names:
            add('work/'+folder+'/'+name, 'reviewed helper or bounded/historical validation source')
    baseline = CURRENT/'work/magix-bootstrap-fix-2026-10-29/magix_workspace.py'
    add('work/magix-workspace-launcher/magix_workspace.py', 'exact deployed bootstrap-corrected baseline', source=baseline)

    pins = {
        PACKAGE+'Helper/project_library.py': '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d',
        PACKAGE+'Client/project_library.py': '3d66f560abee179b861867d9ebea6c64bff21ae240c88712db957496f5d1109d',
        PACKAGE+'Helper/publish_library.py': '056d206fa35f5a0603cd9ab6703daf1b8775d73b123ced8d9d69f88041f1c660',
        PACKAGE+'Client/HELPER-CHECKSUMS.json': '362517385124df2ad6d55363fc0b39d072b4378c11e3f468b1bfebf8bc21f3fc',
        PACKAGE+'Server-Setup/msi_job_process.py': '004b041547291241d2d4674fd08234e68ba26f43b69b77aeaf6d03d38264a60b',
        PACKAGE+'Server-Setup/msi_job_runner.py': 'e6052a9fc82fd970c95435ea31f8d65d6e9bc87160c921685b981252dab0cf8f',
        'work/magix-workspace-launcher/magix_workspace.py': 'c60fb2559a90a2301aa3d741c1d2e676e5ed90559fb40a63e27ef83d75f697aa',
        'work/magix-workspace-launcher/MAGIX-Workspace-Access.ps1': 'f123d1ca489841f415bf8b21cfe799c2edc093a55b96c08d2287e0aee65aa741',
        'work/magix-legacy-path-option/MAGIX-Legacy-Path.ps1': '3a7332607a9fd59d460ca7eb0db8c5e46e892c67b9c9c63a68b03d076120f880',
    }
    prepared = []
    seen = set()
    for source, relative, role in chosen:
        assert relative.casefold() not in seen, relative
        seen.add(relative.casefold())
        target = DEST/relative
        assert not target.exists(), 'Refuse existing destination: '+str(target)
        info = source.lstat()
        assert source.suffix.lower() in ALLOWED and source.is_file()
        assert not getattr(info, 'st_file_attributes', 0) & (0x400|0x1000|0x40000|0x400000), 'Reparse/offline source: '+str(source)
        data = source.read_bytes()
        text = data.decode('utf-8-sig')
        assert '\x00' not in text and len(data)<2_000_000, 'Not bounded text: '+str(source)
        digest = sha(data)
        if relative in pins:
            assert digest == pins[relative], 'Reviewed pin differs: '+relative
        if source.suffix == '.py':
            ast.parse(text, filename=str(source))
        prepared.append((source,target,data,dict(path=relative,source=str(source),bytes=len(data),sha256=digest,role=role,exactCopy=True)))
    assert not (DEST/'SOURCE-MANIFEST.json').exists()
    rows=[]
    for source,target,data,row in prepared:
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(data)
        assert sha(source.read_bytes())==row['sha256']==sha(target.read_bytes())
        rows.append(row)
    seal = json.loads((DEST/(PACKAGE+'Client/HELPER-CHECKSUMS.json')).read_text('utf-8-sig'))
    assert len(seal['files'])==52
    kept={r['path'].removeprefix(PACKAGE+'Client/') for r in rows if r['path'].startswith(PACKAGE+'Client/')}
    deps=[]
    for row in seal['files']:
        if row['path'] in kept:
            data=(DEST/(PACKAGE+'Client/'+row['path'])).read_bytes()
            assert sha(data)==row['sha256'] and len(data)==row['bytes']
        deps.append(dict(**row,inSourceSnapshot=row['path'] in kept,omissionReason=None if row['path'] in kept else ('local runtime validation receipt intentionally excluded' if row['path']=='RUNTIME-VERIFICATION.json' else 'portable runtime binary or standard-library archive intentionally excluded')))
    value=dict(format='interlinked-source-snapshot-v1',createdUtc=datetime.now(timezone.utc).isoformat(),files=rows,summary=dict(exactTextFiles=len(rows),bytes=sum(r['bytes'] for r in rows)),reviewedPins=pins,sealedClient=dict(manifestSha256=sha((DEST/(PACKAGE+'Client/HELPER-CHECKSUMS.json')).read_bytes()),requiredFiles=52,providedFiles=sum(r['inSourceSnapshot'] for r in deps),dependencies=deps),pendingFinalIntegration=['All-project MAGIX module, index generator, new command wrappers and current tests: owner will synchronize after freeze; generated personal index is excluded.'],excluded=['Media and project payloads','Catalogs, personal inventories and generated index payloads','Logs, receipts, active state, lock files and version payloads','Credentials, authentication material, private/public host-key files','Runtime executables, DLLs, PYDs and standard-library archive','Live editable Settings.json; source template is supplied separately'],sourceFilesModified=False,gitActions=False)
    with (DEST/'SOURCE-MANIFEST.json').open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(value['summary']|dict(sealedClientFilesProvided=value['sealedClient']['providedFiles'],sealedClientRequired=52,manifest=str(DEST/'SOURCE-MANIFEST.json'))))

if __name__=='__main__':
    main()
