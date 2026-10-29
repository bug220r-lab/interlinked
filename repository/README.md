# Source distribution

The directory structure intentionally retains the original `outputs/` and `work/` paths inside this distribution. Many tests import their subject relative to those paths. These folders contain selected source files, not the original output payloads or working data.

## Layout

| Path | Purpose |
| --- | --- |
| `outputs/Project-Library-2026-10-29/Helper` | Library publisher, client engine copy, guarded SMB sharing setup |
| `outputs/Project-Library-2026-10-29/Client` | Exact client text sources and original 52-file checksum manifest |
| `outputs/Project-Library-2026-10-29/Server-Setup` | Windows OpenSSH, direct Ethernet, firewall, and background-job helpers |
| `work/project-library-catalog` | Original catalog construction source |
| `work/project-library-tests`, `work/project-library-sharing` | Library and publisher tests |
| `work/msi-server-conversion` | Job-process, setup-guard and historical host-integration tests |
| `work/magix-workspace-launcher` | Exact deployed MAGIX baseline, access helper, command wrappers and local tests |
| `work/magix-all-projects/candidate` | Deployed all-project launcher, current wrappers, index generator and adapted test copies |
| `work/magix-bootstrap-fix-2026-10-29` | Exact baseline duplicate required by relative test imports |
| `work/magix-legacy-path-option` | Exact Stage1 helper imported and hash-checked by the launcher |
| `work/ssh-job-verification`, `work/new-pc-workflow` | Explicit synthetic acceptance-test helpers; live plans and results excluded |
| `SOURCE-MANIFEST.json` | Exact source paths, copied-byte hashes, pinned dependencies and omissions |
| `MAGIX-EXTENSION-MANIFEST.json` | Final extension provenance, including the two path-only test adaptations |
| `docs/MAGIX-WORKSPACE-START-HERE.txt` | Current all-project workflow guide |

The frozen all-project MAGIX extension is integrated. It lists 43 project families and 276 project/recovery entries, fetches only the selected family, and supports six finite reviewed legacy roots. The baseline, Stage1, Access and sealed client files remain unchanged. `SOURCE-MANIFEST.json` preserves the initial 83-file capture; the extension manifest completes its historical pending integration item, and `DISTRIBUTION-MANIFEST.json` covers the final source distribution. The generated personal picker index is excluded.

Recorded validation is 46 local lifecycle checks, 7 launch-path checks and 8 independent review checks. Deployment was hash-verified without changing workspace state. Subsequent actual new-PC protocol activation passed readback: the previous owner was preserved, installation/Stage1/access records were unchanged, and mappings were UNBOUND. The user confirmed that the 43-family picker appears on the new PC. Playback/export of additional families is not established. The original project completed actual open/play/close/DONE, save, verify and explicit release before this upgrade.

## Runtime and deployment requirements

Use 64-bit Windows with NTFS, Windows PowerShell 5.1 and Python 3.12. The Python code uses the standard library, including Windows-specific APIs. The optional editor integration requires a licensed, compatible MAGIX installation and its own codecs/assets.

The original client seal contains **52 files**. This source snapshot includes **8 sealed text files**, plus the checksum manifest itself. The remaining **44 files** are the portable Python executable/DLL/PYD/standard-library payload and the original runtime verification receipt. `SOURCE-MANIFEST.json` lists every required path, size and SHA-256. The runtime license and `python312._pth` are preserved as exact text.

The production launcher requires all 52 original sealed files. A regular Python installation can run source-level tests, but does not satisfy that production seal. Supply the original verified kit separately, or build and review a new runtime package and intentionally update all dependent pins. Do not remove a checksum check or substitute a fabricated runtime receipt.

`Client/Settings.example.json` is a new configuration example. The live editable `Settings.json` is excluded. The regular client can use its default local AppData cache; the current MSI job helper deliberately accepts the separate pinned MSI working cache. Changing the client setting alone does not change that job policy.

An existing installation uses this external launcher layout:

```text
kit/
  magix_workspace.py
  magix_projects.py
  MAGIX-All-Projects-Index.json # private reviewed index supplied separately
  MAGIX-Workspace-Access.ps1
  MAGIX-Legacy-Path.ps1
  MAGIX-Local-Workspace.cmd
  Set-Up-MAGIX-Workspace.cmd
  Project-Library-Client/       # complete sealed kit supplied separately
```

The source locations differ from that deployment layout. Use the current command wrappers from `work/magix-all-projects/candidate`, and place the exact baseline, Stage1 and Access helpers next to the launcher. Ownership receipts, an unbound legacy Windows profile, the selected cache, private index, host catalog and share configuration must also satisfy the current guards. This repository does not include those live inputs.

## Bootstrap order

1. Review the machine, account, volume, path and network constants. Build a new explicit source selection and catalog from current local metadata. The historical builders expect external inventory inputs that are intentionally excluded.
2. Review the publisher's dry run. Publish into a newly owned host directory on the source volume; keep the publisher lock and source validation intact. Original ordinary files remain shared hardlinks and may change when the originals change. SQLite publication uses a separate consistent snapshot.
3. Configure the read-only library share and separate Returns share with the guarded setup helper. Supply and verify the complete client package, then create its editable settings.
4. Use the client's explicit fetch, work, save, verify and release operations. Saved versions may depend on the original library; Returns alone is not a self-contained backup.
5. Set up the MAGIX mapping only after reviewing its finite allowed roots and ownership checks. Save new work inside the selected cached `Files` tree or its mapped legacy path; files saved elsewhere are outside save-back coverage. Close MAGIX and confirm DONE before save/verify or explicit free-space actions.
6. For host compute, review and apply the native Windows OpenSSH setup on the intended host, then verify the actual client connection and a fresh synthetic job. The helpers use the Windows optional capability, scoped firewall rules, explicit ownership and bounded recovery state. Never reuse historical one-shot acceptance plans or reset state merely to get past a guard.

The SSH service accepts only the configured account. The setup scripts do not store passwords or copy private keys. Task jobs use the existing logged-on user's limited interactive token and are designed to outlive the SSH connection while that user remains logged on. Actual disconnect survival was not separately tested. The job object contains ordinary descendants, not work delegated to unrelated services, WMI or separately registered tasks. Do not use those external launch mechanisms from these jobs.

MAGIX dependency extraction is heuristic. Same-basename candidates are unverified alternatives, cloud-only files can remain deferred, and old project paths or installed assets may still require attention. Automatic `.MV_` backups should be opened using MAGIX's **Load backup project** action, not renamed to `.MVP`. See the [MAGIX backup documentation](https://help.magix-hub.com/video/videodlx/en/content/topics/backupproject.htm).

## Tests

Run the integrity verifier first, from the workspace root:

```powershell
python -B repository\verify_source_snapshot.py
```

For the following local fixture tests, change into `repository`, use a normal Windows process and Python 3.12, and keep the directory layout intact. These tests create their own small temporary files. The publisher test creates actual hardlinks and SQLite fixtures; the MAGIX test creates local junction fixtures. A sandbox that denies those operations is not a useful test environment.

```powershell
python -B work\project-library-tests\test_project_library.py
python -B work\project-library-sharing\test_client_independent.py
python -B work\project-library-sharing\test_publisher.py
python -B work\msi-server-conversion\test_job_process.py
python -B work\msi-server-conversion\test_job_unc_identity.py
python -B work\magix-workspace-launcher\test_workspace.py
```

The first two tests exercise an owned local fixture library. Process tests launch only their synthetic child processes and test descendant completion/termination. The MAGIX lifecycle test uses synthetic project bytes and does not open the editor or contact production shares.

The all-project tests additionally require a **locally supplied private index** at `work/magix-all-projects/candidate/MAGIX-All-Projects-Index.json`. Supply the original exact index, or rebuild it from the separately retained published catalog using the included generator. The generator pins that catalog's hash, and the launcher pins the resulting index's hash; a different catalog requires a new inventory review, not bypassing those checks.

```powershell
# Replace the catalog argument with your separately retained reviewed file.
python -B work\magix-all-projects\candidate\build_index.py C:\private\catalog.json work\magix-all-projects\candidate\MAGIX-All-Projects-Index.json
python -B work\magix-all-projects\candidate\test_projects.py
python -B work\magix-all-projects\candidate\test_launch_paths.py
```

Run these tests in that order: the launch-path test consumes the first test's generated fixture receipt. Their packaged copies change only the client-engine path assignment to use this repository's Client source. Their test logic is unchanged, and the extension manifest records original and packaged hashes. The private index, fixtures and test receipts are ignored by Git and are not bundled. Packaging checked their syntax and exact one-line adaptations; it did not rerun these private-index-dependent suites.

Other included tests preserve the original review source. Inspect their paths, pins, side effects and prerequisites before running them. Some are intentionally host-bound, consume excluded receipts, or check an earlier hash; notably `test_scheduled_job.ps1` retains its historical runner pin and is not a current portable test command. PowerShell guard tests are not permission to run setup Apply/Restore. The SSH/new-PC acceptance controllers require a newly prepared, reviewed one-shot plan and actual endpoint access. No blanket test-discovery or CI command is provided because mixing those categories would be misleading.

Packaging checks verify exact copied bytes and parse Python source; they are not a new deployment or a new full application acceptance run. The machine-specific code is retained for provenance, not generalized into a fresh installer.

## Excluded material

No media, MAGIX project payloads, personal source catalogs, generated picker indexes, inventories, runtime receipts, ownership state, locks, logs, version payloads, saved credentials, private keys or portable runtime binaries are included. Public host fingerprints or pins embedded in reviewed source are intentional and are not private keys. The root `.gitignore` excludes the working evidence directory and generated/private material. Check any future additions explicitly before staging them.

`assemble_source_snapshot.py` records the initial allowlist and refuses overwriting existing destinations. It is a historical packaging utility, not an installer or a refresh command. `verify_source_snapshot.py` checks the initial imported bytes, final extension and distribution hashes. Keep the manifests current when intentionally changing source files; preserve the exact pinned dependencies unless a new review explicitly updates them.
