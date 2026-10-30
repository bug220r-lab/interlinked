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

For a synthetic visual preview, using an installed Python3.12:

```powershell
python -B repository/app/interlinked_gui.py --demo
```

The demo never invokes the real launcher or reads personal projects. Fake actions are labelled DEMO. Tests create their own tiny files, use fake subprocesses and a loopback HTTP listener:

```powershell
python -B repository/app/test_gui.py --output C:\your-private-test-folder
```

A network-restricted sandbox may block even loopback fixture connections. Do not substitute production operations for these tests. Test receipts contain fixture paths and belong outside tracked source.
