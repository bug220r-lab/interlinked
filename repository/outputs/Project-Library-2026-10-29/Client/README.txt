MSI PROJECT LIBRARY CLIENT
Prepared 2026-10-29

This client fetches selected projects from the old MSI, works from a local
cache, saves changes as new remote versions, and releases verified local
files when requested. It does not install your editing applications, their
licenses, project dependencies, or Python environments.

GET STARTED

Follow START-HERE.txt. Extract the whole client to a local folder, then run
Project-Library.cmd as your normal signed-in Windows user. The included
Python 3.12 runtime needs no installation. The MSI sharing setup must already
be complete, and both computers must be connected to the trusted network.

Defaults are \\MSI\ProjectLibrary (read only), \\MSI\ProjectReturns (returned
versions), and %LOCALAPPDATA%\ProjectLibrary\Cache (local working files).
Settings.json is intentionally editable. Its server value is normally MSI;
cacheRoot may be blank for the default or a full local folder path. Changing
the cache setting does not move, merge, or remove old caches.

PROJECT MENU

1 Fetch project: copies the selected files or latest committed returned
  version into an owned local cache and verifies SHA256 after copying.
  Existing local edits are preserved; conflicting files cause a stop.
2 Open local files: opens the catalog's working folder inside the cache.
3 Status: reports local file counts and saved-version state. Status does
  not verify file contents; use Verify for that.
4 Save changes back: writes a NEW version. Changed/new files receive new
  payloads. Unchanged files can reference verified library originals or a
  previous returned payload. Local deletions are recorded in the new full
  manifest; older originals and returned versions are retained.
5 Verify saved copy: compares local files and remote references against the
  recorded snapshot. New, changed, or missing local files require a new save.
6 Free local space: requires the exact project ID and a committed save.
  It verifies the complete remote version, original-source references, and
  local membership/content, then deletes only verified files under this
  owned cache's Files directory. It keeps ownership, state, and receipts.
  There is no automatic eviction or recursive blind deletion.
7 Open a configured project terminal: opens PowerShell in the selected
  workspace. For The Stats, it sets STATS_DATA_DIR to the cached data folder
  for that terminal and its child processes. It does not alter global
  environment settings or automatically start project code.

WORKING RULES

Use one active working copy per project. Work only in the managed cache,
including on the MSI. Reconfigure or pause background jobs that still write
to the original MSI source folders. Close editors, terminals running jobs,
and other writers before Save, Verify, or Free local space. Close writers
on the MSI too if a source-lock message names an original file.

All regular files under the project's managed Files directory participate
in saves, including hidden files, .git, .env, newly created files, and runtime
output. Nothing is silently discarded as a cache. Private credentials you
create there will therefore be included in the private returned version.
Keep disposable virtual environments/dependency installations outside Files
when possible. Links, junctions, offline placeholders, and unsupported
filesystem objects are rejected rather than silently skipped.

Some catalog entries are large collections instead of individual projects.
The catalog currently contains 135 entries. Review sizes and select only
what you need. The client requires room for the requested payload plus a
2 GiB reserve on the receiving volume and checks space during copying.
Large changes can be blocked when the MSI lacks enough space for a new
version. Existing originals and older versions are not deleted to make room.

For The Stats, use the configured project terminal when starting its tools
so STATS_DATA_DIR selects cached data. Rebuild application environments as
needed on the new PC; the bundled helper runtime is not a project environment.

MAGIX files are copied byte-for-byte. Some projects still reference old
absolute paths, unavailable cloud files, or unresolved media. Open the local
project and relink only to verified assets inside its cache when needed.
The helper does not rewrite project files or promise every project will run.

BACKUP LIMITS

Most initial library files are live hard links to existing files on the MSI.
A write to an original also changes its library bytes. The read-only SMB
share prevents client writes through that share, but does not freeze local
MSI originals. Selected active databases are published as snapshots.

Returned versions may reference unchanged library originals and earlier
returned payloads. They are not independent, self-contained archival copies.
Editing or deleting those referenced files can invalidate old versions.
Save/release detects existing source/reference conflicts; it cannot prevent
someone from damaging a reference after a completed operation. Keep the
originals protected and maintain a separate offline or off-machine backup.
Do not edit files directly in either shared library/returns folder.

INTERRUPTIONS AND CONFLICTS

Fetch interrupted: rerun Fetch with the same project and cache. Owned partial
bytes are checked against the source before continuation. A changed source
or conflicting local file is preserved and reported, not overwritten.

Save interrupted: rerun Save. A fully prepared pending save is recovered only
after checking its exact manifest, remote references, and local files. An
incomplete version never becomes HEAD. Incomplete remote version folders may
remain; do not remove them without reviewing ownership and references.

Free local space interrupted: rerun Free and type the project ID again. The
helper checks the complete remote version and all remaining local files
before continuing. Changed/new files block further deletion.

HEAD/source conflict: stop and keep the current cache. Another working copy
or original changed. There is no automatic merge. Fetch into a separate new
cache root if needed to compare versions, then resolve the conflict deliberately.
Do not delete marker/state files or edit HEAD to force an operation through.

Missing space, unavailable share, locked file, corrupt reference, or local
changes cause a blocked result. Read the named path and error. Progress is
printed during long copy/hash passes. Verification can reread large files
and may take substantial time. Keep the MSI awake throughout.

Logs are saved beside this client in Logs. Cache events and state remain
beside each project's Files directory. Retain these when asking for help.

INTEGRITY AND VALIDATION

The launcher verifies HELPER-CHECKSUMS.json before starting. It covers shipped
runtime, code, and documents. Settings.json and generated Logs are excluded
because they are user/runtime data. If a covered file differs, recopy the
published package; do not bypass the check. The checksum list detects changed
files but is not a cryptographic signature or independent trust anchor.

The frozen engine passed 22 local functional fixtures, 11 independent
functional fixtures, and a 60,000 simultaneous native-handle fixture. Tests
covered save/release crash recovery, changed/new/deleted files, corruption,
conflicts, path guards, low space, long Unicode paths, and safe resume.
No production network copy, deletion, or share setup was performed by these
client tests. Synthetic hardlink creation was unavailable in the sandbox;
confinement testing used an ordinary file outside the cache instead.

Advanced CLI: Runtime\python.exe -B -X utf8 project_library.py --help
Commands: list; fetch ID; status ID; save ID; verify ID;
          release ID --confirm ID
Options --library, --returns, and --cache precede the command.
