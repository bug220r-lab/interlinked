# Reviewed deployment helpers

These files support the existing MSI / THIS-MACHINE installation. They are not a portable installer for arbitrary computers. Private packages, inventory reports, presets and font binaries are supplied separately and must not be committed here.

## MAGIX presets and fonts

`Check-MAGIX-Presets-and-Fonts.cmd` runs `Collect-MAGIX-Inventory.ps1` as the current user. It lists file metadata in bounded MAGIX locations and registered font names. It does not follow reparse directories, read project payloads, change settings or install fonts. Unavailable/skipped roots remain visible in its report.

`Build-MAGIX-Preset-Definition-Index.py` builds a private comparison index from preserved inventory snapshots and bounded, read-locked definition files. `Compare-MAGIX-Preset-Definitions.ps1` verifies that index's pinned hash and compares the existing local definitions on THIS-MACHINE. It reports matches, missing files, differences and read errors. It never applies a replacement. Large media and movie-template bundles are outside this definition hash comparison.

`Import-MAGIX-Presets.ps1` and `import_magix_presets.py` apply an explicitly prepared private package containing 24 custom presets and the four exact League Gothic styles used on MSI. The manifest hash, interpreter dependencies, target computer, Jude account SID, application version, allowed preset locations and font hashes are checked. MAGIX must be closed. Applying requires UAC approval because the original content folders are under Program Files and the fonts are registered for all users.

The importer adds absent files only. An identical file can be skipped; a different existing file stops preflight. It does not overwrite presets, edit projects, delete files, reset font caches or change services. Receipts preserve partial additions if an operation fails. A successful receipt confirms file hashes, font registration and Windows loading, not visual rendering inside MAGIX.

The package uses the existing MSI font files to preserve their exact version. The League Gothic license is included with that private package. Other font families found in historical project font tables are reported separately; they are not silently substituted or installed by this importer.

## GUI publication

`Publish-Interlinked-GUI.ps1` verifies the preceding deployment hashes and the unchanged launcher/index pins, then acquires the GUI instance lock before replacing its allowlisted app files. Existing versions are retained as backups. Finish the MAGIX session and stop INTERLINKED before publishing; closing its browser tab alone does not stop its service.

## Desktop publication on THIS-MACHINE

The current source requires a twenty-six-member v10 GUI seal and the matching
40-member desktop publication plan (41 with the explicit launcher migration).
The release descriptions below preserve the
earlier package history. Rebuild and review current application and node packages
before installation; new exclusion and remote-worker recovery capabilities must
be available on both ends. Existing installation, session and ownership guards
still apply. Publishing source to GitHub does not run these helpers or migrate
private configuration, history, task journals, or model evidence.

The current `-MigrateBuddyLauncher` transition is bound to the reviewed installed
launcher `b1ca74f6169a39199f464c7420633013fc0d945b58e624dfca53c78997dcb819`
and Buddy `ed897501febcaa7084c84508dd86ceba1122113d2e0da06843e1b077e1973bfc`.
Its prospective launcher is
`0650ab24e8d6b36ff2bd139408fcb565009e7f66d2952bec5dc91742dd7ae63f`,
pinning Buddy `f4dc148f5be75929d4e323440314956f56d551e4354086d3149a1bdfa0ab1d49`.
Only the launcher's single Buddy hash changes. Ordinary full publication also
validates the complete GUI-to-launcher-to-Buddy dependency chain. These are
source constants for a future reviewed package; no installed files are changed
by source verification. The earlier `93173fad…` to `b1ca74f…` transition remains
historical evidence, not the baseline for this update.

The full matched application/worker package is required for this release.
The narrow cooler publisher recognizes exact v10 baselines for later rendering
updates; it does not deliver AI/history protocol or launcher changes.

The Violet Web polish ships the matching v9 backend and native worker through
the complete 35-member desktop publisher. The desktop host, artwork, launchers,
GUI controls and unrelated companions can remain byte-identical. Private
worker-only display metadata does not expand the public MASTER/plugin snapshot.
Publication still requires normal application shutdown and all existing session,
ownership, source and process guards. The protected cooler startup record needs
its separate exact old/new app and worker binding update; sensor payloads,
scheduled tasks and TRCC settings remain independent and preserved.

MSI resource telemetry requires its separately sealed AI-node update, preserving
pairing credentials, TLS identity, inference-policy pins, reviewed checks and
retained worker/request journals. The MSI CPU provider has separate machine/SID
and vendor pins, protected output and one fixed sensor-only task; see
`../desktop/MSI-SENSORS.md`. Source tests, staged bytes, installed readback and
human physical-display acceptance are separate delivery evidence.

The cooler AI-flow update retains the exact twenty-one-member v9 GUI schema and
publishes its matching backend, web controls and native worker together. The
existing full publisher applies its exact reviewed Buddy/launcher pin transition;
only the launcher's `BUDDY_SHA` value may differ. A renderer-only publication does
not deliver the layout setting, AI observations or recovery scheduler. Protected
automatic-startup app/worker bindings require a separate exact old/new-hash review
with preserved ownership, backup, sensor payload and scheduled-task state.

The AI workspace release advances the current allowlist to 32 publication members
and the prospective GUI seal to eighteen-member v8. Exact v1–v7 baseline schemas
remain recognized. The three added members are `interlinked_ai.py`,
`interlinked_ai_node.py` and `interlinked_ai_workspaces.py`; existing lock and session
guards still apply. The standalone MSI node package and per-user TLS transport
configuration have separate explicit setup. Later paragraphs describe earlier releases.

`Publish-Interlinked-Desktop.ps1` is the local publisher for the fixed THIS-MACHINE/Jude kit. The historical MSI publisher above retains its original machine/path provenance. The current publisher takes a reviewed 29-file plan and exact plan SHA-256, checks the original 52-file Python/client seal and launcher/index pins, recognizes exact v1-v7 GUI baseline schemas and requires the prospective fifteen-file v7 seal plus desktop seal, requires unbound workspace journals, and acquires both existing GUI and workflow locks. It refuses held locks, active MW5/folder/Stats journals, and running local MAGIX/MW5/desktop/MASTER-worker processes. It does not stop editing applications on MSI.

The exact added app members are `interlinked_master.py`, `master_hook.py`, `master_bridge.py`, the read-only `interlinked_jobs.py` observer and `interlinked_buddy.py` companion; the desktop package additionally contains `INTERLINKED.Master.Worker.exe`, its configuration and the approved `Violet.png`. The desktop build requires the private approved v2 atlas through `-VioletAtlas`; its seal covers all ten package payloads. Vendor USB/sensor binaries are external installed dependencies, not publication payloads. Device pins, hook trust, telemetry, project registrations and startup preferences are outside Git and this publisher's file allowlist. Adding the observer source does not update the installed setup; publication and actual SSH/status acceptance remain separate actions.

The current publisher pins the accepted Buddy-capable MAGIX launcher. Schema recognition does not authorize launcher migration: the historical all-project launcher, frozen workspace implementation, original client/runtime seal and historical publication helpers remain unchanged. The publisher preserves the accepted launcher rather than including it as a replaceable payload.

The Media inbox report update advances that current launcher dependency pin together with its Buddy module pin and the GUI's launcher pin. Its separately reviewed narrow publisher in ED-IT Buddy updates only the companion module, launcher, GUI dependency pin and Buddy report block, using distinct source and installed snapshots. It retains the exact fifteen-member v7 GUI seal and the installed MASTER, desktop worker, cooler startup and sensor identities. The full desktop and cooler publishers require this accepted launcher after narrow publication; their replacement allowlists are unchanged. Historical deployment pins and provenance manifests remain exact. Active Stats, MW5, folder or MAGIX sessions and desktop/worker processes block publication until normal completion.

The default is preflight only. Add `-Apply` to install after the [desktop tests](../desktop/README.md) pass. New files are staged and hash-read back; replacements retain exact backups, and `INTERLINKED.cmd` is checked last. On the first desktop installation, `INTERLINKED-Browser.cmd` preserves the exact previous launcher; subsequent updates preserve that existing browser fallback byte-for-byte. Private plans and receipts belong under ignored `.handoff/`. The publisher does not write project caches, mappings, ownership, reports, media, or the sealed client/runtime.

`Test-Interlinked-Desktop-Publication.ps1 -OutputDirectory <private-fixture-folder>` exercises the internal publication engine against synthetic roots. Production machine and path pins remain fixed in the public entry point.

The Stats release adds only `interlinked_stats.py` and its atomically contained
process helper to the application allowlist. Earlier GUI seal schemas remain
valid baseline evidence; the current prospective seal must be exact v7. Stats source,
its environment, connection metadata, MASTER registration and managed data are
separate from app publication. The publisher refuses unfinished Stats journals
and does not reconcile or restore them. An existing desktop binary can be
retained byte-for-byte when only the Python/web application changes.

The configuration recovery release adds `interlinked_configuration.py` alongside
the Stats companions, yielding an exact fifteen-file v7 GUI seal and 29-member
publication plan. Earlier v1-v6 seals remain recognized only as exact baselines.
The package includes the tray validity fix; unavailable or unknown lifecycle
preferences never apply startup/background defaults. Configuration history and
attention records are owned runtime metadata and are not publication payloads.
Publication continues to refuse active application/session locks and preserves
all project caches, runtime ownership, device pins and historical manifests.

The Violet AI plugin follow-on requires an exact twenty-one-member v9 GUI seal,
adding `interlinked_ai_bridge.py`, `interlinked_ai_ipc.py` and `ai_bridge.py`.
The publisher accepts sealed historical v1–v8 installations and retains the same
session, file identity and process guards. Plugin packaging is separate from app
publication; an enabled plugin needs the matching installed coordinator. Existing
app-hash-bound cooler startup remains a separate installation dependency.

The combined Buddy AI release uses the same v9 GUI seal. Its explicitly selected
`-MigrateBuddyLauncher` publication adds one launcher member to the usual 35-file
plan. Fixed before/after hashes permit only the single `BUDDY_SHA` value to change;
the new value must match the sealed Buddy companion, and the GUI must pin the
new launcher. Any other launcher edit, dependency replacement, active session or
changed baseline is refused. The original launcher is retained in the publication
backup and migration metadata is recorded in its receipt. Ordinary later updates
retain the new accepted launcher. Historical launcher and provenance manifests
remain unchanged.
