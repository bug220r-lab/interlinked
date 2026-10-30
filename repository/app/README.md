# INTERLINKED browser interface

This standard-library Python service wraps the unchanged reviewed MAGIX CLI. It binds only to `127.0.0.1`, opens an ephemeral local browser session, and keeps the launcher process alive independently of browser tabs. A native service window must remain open during work.

Production layout is additive:

```text
kit/INTERLINKED.cmd
kit/INTERLINKED-App/interlinked_gui.py
kit/INTERLINKED-App/GUI-CHECKSUMS.json
kit/INTERLINKED-App/web/{index.html,app.js,style.css}
kit/MAGIX-Media-Audit.json
kit/magix_projects.py                  # unchanged, pinned dependency
kit/magix_workspace.py                 # unchanged, pinned dependency
kit/MAGIX-All-Projects-Index.json       # private, pinned snapshot
kit/Project-Library-Client/Runtime/python.exe
```

The GUI seal uses `format: interlinked-gui-checksums-v1`, a `files` object mapping the four app/web paths to their exact SHA-256 values, and `auditSha256` for the private sidecar. The production kit and full sealed client must already exist. This source does not install or change system settings.

Use Prepare/Open, save and close MAGIX, then choose **I saved and closed MAGIX**. That response does not save to MSI. Use **Save to MSI + verify** afterward; Free local space requires the exact selected project ID. The service refuses to exit while an operation is active. Closing a browser tab does not close stdin, terminate the child, save, or release files. Forced service/process termination cannot preserve a process-held lock: close MAGIX and use the reviewed console Recover workflow before continuing.

The picker includes reviewed entries and safely enumerated local Save As `.MVP`, `.MV_`, and `.MVD` files under the six supported physical cache roots. Enumeration reads metadata only, rejects links/cloud placeholders, caps traversal at25,000 items and128 directory levels, and revalidates selected local entries before dispatch. READY deletions are never restored automatically. Legacy console option L remains available for intentionally supported local paths outside this GUI discovery scope.

Media audit rows are historical reference strings, not proof of active missing clips or playback compatibility. Candidate paths and manual proposals are displayed without applying substitutions, converting media, modifying project bytes, or opening arbitrary filesystem paths. Reports and private catalog/index data are supplied separately and must not be committed to the source repository.

Logs, startup evidence and operation receipts are stored under the kit's owned `INTERLINKED-GUI-State`, outside project Files. The startup receipt records verified application/audit hashes without the capability token. HTTP APIs require the per-run token and exact origin/host checks; static HTML/CSS/JS are public local assets. One native instance lock prevents duplicate GUI servers for the same kit. The existing global workflow and cache locks remain authoritative against the console launcher.

Prepare/Open displays pending work, recent per-file fetch/verification output, and confirmed terminal errors directly below its button. A held workspace lock explains how to finish the older console session; it never bypasses the lock or retries automatically. Activity details remain available through an explicit link. Error/progress presentation has 18 synthetic DOM/model checks and 15 independent checks. Run its isolated tests with `node repository/app/test_open_feedback.js`.

For a synthetic visual preview, using an installed Python3.12:

```powershell
python -B repository/app/interlinked_gui.py --demo
```

The demo never invokes the real launcher or reads personal projects. Fake actions are labelled DEMO. Tests create their own tiny files, use fake subprocesses and a loopback HTTP listener:

```powershell
python -B repository/app/test_gui.py --output C:\your-private-test-folder
```

A network-restricted sandbox may block even loopback fixture connections. Do not substitute production operations for these tests. Test receipts contain fixture paths and belong outside tracked source.

### User-reported missing media

The compact **Reported missing media** panel accepts a filename/path and an optional multiline note during a MAGIX session. It never reads that path, watches dialogs, changes media, or retries a fetch. Audit-row flags derive their text from the selected project's reviewed reference. Reported missing, user-marked unavailable, and user-marked resolved are observations, not verified repairs.

Reports are stored outside all project caches in the owned `INTERLINKED-GUI-State/missing-media.events.jsonl`, with a separate ownership marker and lock. Each append is hash-chained, flushed, fsynced, and read back. Missing, torn, corrupt, or hardlinked ledger data fails closed and remains preserved. Exact case-normalized paths dedupe within one project; a filename-only report stays separate from a full path. JSON exports include original text and full project event history; CSV prefixes display cells with `Text: ` for spreadsheet use.

For a reviewed one-time observation import, load `MissingMediaLedger(state_path, exact_gui_owner_object, indexed_project_ids)` from `interlinked_gui.py` and call `.report(project_id, literal_path, request_id=<stable UUID>, source='user_dialog', note=<evidence/uncertainty>)`. This independent ledger lock does not acquire the GUI-instance, project or MAGIX workflow locks. Import only actual observations; do not propagate one popup to other projects, similar Unicode spellings, or sidecars.

`test_missing_media.py --output <fixture-folder>` uses synthetic local files only, including corrupt/torn history, hardlink rejection, UUID retries, active-session and shutdown ordering checks. `test_missing_panel.js <receipt.json>` uses a synthetic DOM. Existing `test_gui.py` covers authenticated report/export routes with fake children and a loopback fixture.
