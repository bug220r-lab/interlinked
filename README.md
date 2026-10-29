# INTERLINKED

INTERLINKED is a Windows project library for keeping large project collections on a host PC, fetching one working set to another PC, and saving verified versions back before freeing local space. It also includes guarded SSH setup, explicit background jobs on the host, and a MAGIX workspace launcher.

The source distribution is in [repository](repository/README.md). It preserves the reviewed implementation and test files. Media, personal project catalogs, deployment state, credentials, private keys, and the portable Python payload are excluded. Reviewed source may retain intentional public host pins.

This is a source snapshot of an existing installation. Several helpers deliberately pin machine identities, file hashes, paths, and ownership receipts. Cloning this repository does not produce a new working deployment by itself. Read the dependency and bootstrap notes before running setup helpers; keep the ownership guards intact.

## Components

- **Library host:** publishes selected local files through an owned read-only SMB library. Same-volume hardlinks avoid a second payload copy; the active SQLite database receives a consistent snapshot.
- **Working cache:** fetches selected project files, records edits and deletions in a version, verifies saved content, and releases only an explicitly confirmed owned cache.
- **Host jobs:** starts an explicit, limited scheduled task for code within the managed cache. A Windows Job Object contains ordinary process descendants and the cache lock remains held until they finish.
- **MAGIX workspace:** selects from 43 reviewed project families and 276 project/recovery entries, fetches one working set, and temporarily maps six reviewed legacy roots to its local cache. The user closes MAGIX and confirms DONE before saving or releasing the cache.

The library shares are storage and versioning services, not an automatic pool of CPU or RAM. Jobs execute only when requested. Saved versions can reference original library content, so keep the host library and independent backups.

The all-project launcher passed 46 local lifecycle checks, 7 launch-path checks and 8 independent review checks. Deployment and actual new-PC protocol activation passed readback, with the original installation, Stage1 and access records preserved. The user confirmed that the 43-family picker appears on the new PC. Playback of additional families remains untested. One original project already completed the actual open/play/close/DONE, save, verify and release workflow. Playback, export and dependency completeness across every project remain unverified. The private index is supplied separately; it is not committed here.

## Source integrity

From the repository root with Python 3.12 available:

```powershell
python -B repository\verify_source_snapshot.py
```

This checks the imported source manifest without importing application modules, using the network, or changing system settings. See [tests and deployment requirements](repository/README.md) for the separate Windows fixture tests.
