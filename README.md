# INTERLINKED

INTERLINKED is a Windows project library for keeping large project collections on a host PC, fetching one working set to another PC, and saving verified versions back before freeing local space. It also includes guarded SSH setup, explicit background jobs on the host, and a MAGIX workspace launcher.

The source distribution is in [repository](repository/README.md). It preserves the reviewed implementation and test files. Media, personal project catalogs, deployment state, credentials, private keys, and the portable Python payload are excluded. Reviewed source may retain intentional public host pins.

This is a source snapshot of an existing installation. Several helpers deliberately pin machine identities, file hashes, paths, and ownership receipts. Cloning this repository does not produce a new working deployment by itself. Read the dependency and bootstrap notes before running setup helpers; keep the ownership guards intact.

## Components

- **Library host:** publishes selected local files through an owned read-only SMB library. Same-volume hardlinks avoid a second payload copy; the active SQLite database receives a consistent snapshot.
- **Working cache:** fetches selected project files, records edits and deletions in a version, verifies saved content, and releases only an explicitly confirmed owned cache.
- **Host jobs:** starts an explicit, limited scheduled task for code within the managed cache. A Windows Job Object contains ordinary process descendants and the cache lock remains held until they finish.
- **MAGIX workspace:** selects from 43 reviewed project families and 276 project/recovery entries, fetches one working set, and temporarily maps six reviewed legacy roots to its local cache. The user closes MAGIX and confirms DONE before saving or releasing the cache.
- **Matrix-themed GUI:** a local browser interface for project search, entry selection, prepare/open, session completion, save/verify, explicit release and recovery. It wraps the accepted launcher and retains its locks and checks. The interface is served only on the local PC.
- **Media review:** a per-family audit distinguishes exact covered paths, unverified filename alternatives, deferred media and references with no located candidate. It does not rewrite proprietary project files or substitute media automatically.
- **Missing-media reports:** record actual MAGIX dialog filenames and review notes in an append-only journal outside project caches. JSON preserves the event history; CSV provides a spreadsheet-safe display export. The GUI does not observe remote screens automatically.
- **Preset migration:** read-only content/font inventories and definition comparisons, plus a guarded add-only importer for the reviewed custom MAGIX presets and matching title fonts. See [deployment notes](repository/deployment/README.md); private payloads are excluded.

The library shares are storage and versioning services, not an automatic pool of CPU or RAM. Jobs execute only when requested. Saved versions can reference original library content, so keep the host library and independent backups.

The all-project launcher passed 46 local lifecycle checks, 7 launch-path checks and 8 independent review checks. Deployment and actual new-PC protocol activation passed readback, with the original installation, Stage1 and access records preserved. The user confirmed that the 43-family picker appears on the new PC. Playback of additional families remains untested. One original project already completed the actual open/play/close/DONE, save, verify and release workflow. Playback, export and dependency completeness across every project remain unverified. The private index is supplied separately; it is not committed here.

The subsequent source-library media audit checked 1,742 selected files: all were available, matched the catalog metadata, and shared physical NTFS identity with their published files. All 316 available exact user-file reference paths were already selected and covered by the six mappings; 268 were audio, video or images. No additional exact-path repair was justified. Historical or ambiguous reference strings remain review items, not confirmed active missing clips. The audit did not hash media, inspect edited client caches, or certify playback. Personal audit reports are excluded from Git.

On the installed client, open **INTERLINKED.cmd** from the MSI Project Library folder. For a synthetic UI preview from this source checkout:

```powershell
python -B repository\app\interlinked_gui.py --demo
```

The preview never runs MAGIX or accesses personal caches. See [GUI notes](repository/app/README.md) for service lifetime, testing and deployment details.

The GUI passed 38 isolated implementation checks and 35 independent review checks, followed by synthetic browser workflow checks. Its nine-file client installation was hash-verified, and the production startup receipt and user confirmation established that the reviewed GUI and all 43 families load on THIS-MACHINE. The media auditor passed 13 logic tests and 15 independent report-consistency checks. These checks do not establish playback of every family or a complete editing workflow through the new GUI.

The missing-media extension passed 51 ledger checks, 46 backend/API checks, 13 panel checks and 21 independent checks, with the 18 progress-feedback checks retained. Its installed update was hash-verified and the user confirmed that both supplied missing-file observations appear on the new PC. Private report text stays outside this repository.

The subsequent preset comparison matched 6,804 existing definitions on the new PC to MSI by hash, with zero read errors. A separate add-only import installed 24 missing custom presets and four matching League Gothic styles. The new-PC receipt verified every addition and Windows font registration/loading, with no overwrite. The user confirmed both custom title presets appear and the font renders correctly. Existing Music Editor configuration differences and generated performance state were retained. These checks do not certify every preset's rendering or large media/template payload.

## Source integrity

From the repository root with Python 3.12 available:

```powershell
python -B repository\verify_source_snapshot.py
```

This checks the imported source manifest without importing application modules, using the network, or changing system settings. See [tests and deployment requirements](repository/README.md) for the separate Windows fixture tests.
