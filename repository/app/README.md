# INTERLINKED interface and local service

This standard-library Python service wraps the unchanged reviewed MAGIX CLI. It binds only to `127.0.0.1`. The [WebView2 desktop host](../desktop/README.md) starts it with `--desktop`, receives its private startup URL through a captured pipe, and displays the existing interface in its own window. `INTERLINKED.cmd` launches the desktop host. `INTERLINKED-Browser.cmd` preserves the previous browser/service-window entry point for explicit fallback use; keep that service window open during work.

Production layout is additive:

```text
kit/INTERLINKED.cmd
kit/INTERLINKED-Browser.cmd
kit/INTERLINKED-Desktop/INTERLINKED.exe # separately sealed desktop host and SDK DLLs
kit/INTERLINKED-App/interlinked_gui.py
kit/INTERLINKED-App/GUI-CHECKSUMS.json
kit/INTERLINKED-App/web/{index.html,app.js,style.css}
kit/MAGIX-Media-Audit.json
kit/magix_projects.py                  # unchanged, pinned dependency
kit/magix_workspace.py                 # unchanged, pinned dependency
kit/MAGIX-All-Projects-Index.json       # private, pinned snapshot
kit/Project-Library-Client/Runtime/python.exe
```

The current GUI seal uses `format: interlinked-gui-checksums-v5`, a `files` object mapping the four existing Python modules, `interlinked_master.py`, `master_hook.py`, `master_bridge.py`, `interlinked_jobs.py`, `interlinked_buddy.py` and the three web assets to their exact SHA-256 values, and `auditSha256` for the unchanged private sidecar. Before importing companions, the production service checks this seal and the complete original 52-file client/runtime seal. The production kit and full sealed client must already exist. Source-only previews do not install or change system settings.

Use Prepare/Open, save and close MAGIX, then choose **I saved and closed MAGIX**. That response does not save to MSI. Use **Save to MSI + verify** afterward; Free local space requires the exact selected project ID. In background mode, closing the desktop window hides it in the tray. Explicit Exit interface retains the existing operation/session shutdown guards; with background mode disabled, closing requests that guarded exit. In browser mode, closing a tab does not close stdin, terminate the child, save, or release files. Forced service/process termination cannot preserve a process-held lock: close MAGIX and use the reviewed console Recover workflow before continuing.

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

`test_desktop_startup.py --output <fixture-folder>` checks the real-process demo handshake, browser suppression, capability/origin guards, duplicate instance refusal, busy shutdown refusal, explicit Done and graceful exit. The desktop host also has a real WebView2 smoke test; see its README. None of these synthetic tests certify MAGIX playback or real save-back.

## MASTER command deck

MASTER adds the project command deck and the exact 480 × 480 cooler-frame preview. Its passive collector never starts a project job or acquires a project/workflow lease. The separate native worker renders and encodes frames independently of window visibility. It retains one latest frame, drops obsolete snapshots, and starts without LCD ownership. Only **Connect display** requests the cooler; TRCC startup remains unchanged. Production worker executable/configuration and vendor dependencies must match their reviewed private seals. HTTP settings cannot provide executable paths, disable pins or launch project actions.

`GET /api/master/state`, `GET /api/master/frame`, `GET /api/master/settings`, `POST /api/master/settings` and `POST /api/master/display` reuse the per-run token, exact Host and origin checks. Frame responses are private JPEGs with `no-store`; no frame or a stopped worker returns503. A corrupt MASTER configuration produces an explicit unavailable MASTER service while the library APIs remain usable. Display failures cannot finish, save or release projects.

Settings/project registrations live in owned `INTERLINKED-GUI-State/master`, outside managed caches and Git. The default Git roots derive from the Windows profile: `OneDrive/Documents/ED-IT Buddy`, `Documents/Codex/MW5-MOD/Source` and `Documents/Codex/INTERLINKED`. MW5 activity also matches its parent project folder. Additional ordinary local project roots can be registered in MASTER. Private helper pins stay in server-only `master/device.json`.

The bounded metadata feed is `%LOCALAPPDATA%/INTERLINKED/MASTER-status`; demo feeds are isolated beneath the requested fixture state. Lifecycle hooks retain session/project identity, event type and observation time, discarding prompts, transcripts, tool arguments and results. Unregistered roots are dropped before spooling. Duplicate/out-of-order events cannot reverse a terminal event; quiet/stale sources become unknown. Git reads are local and disable optional locks. Turn completion never establishes a passing build/test: explicit receipts require the exact tested clean revision. The passive ED-IT Buddy feed reads its existing coordinator publisher without another connection or lease.

Violet's local bridge exposes only `get_master_status` and `publish_violet_status`. Its reduced snapshot omits project paths, capture-window titles, tokens and raw operation output. Published activity is labelled **Dot-reported**, expires after two minutes, and remains separate from measured build/test evidence. See the [bridge and hook setup](../master-plugin/README.md). Actual Dot access requires a real tool call; fixture tests cannot establish account connectivity. Command hooks are supported only in applicable local workflows; unsupported cloud workflows stay visibly unconnected.

Live-mini mode requires selecting and calibrating the actual visible mini window on a black capture area. Capture is disabled on reopen. Hidden, covered, moved or unavailable sources invalidate the mode. Temperatures require fresh verified publisher readings; missing/stale readings display `—`. Optional sensor setup uses the explicit authenticated `POST /api/master/display {"action":"enable_sensors"}` only after production LCD connection; synthetic demos cannot request elevated setup. It does not change the privileges of the main application.

Run isolated checks in fresh private folders:

```powershell
python -B repository/app/test_master.py --output .handoff/master-check --worker C:\private\INTERLINKED.Master.Worker.exe
python -B repository/app/test_master_http.py --output .handoff/master-http-check --worker C:\private\INTERLINKED.Master.Worker.exe
node repository/app/test_master_ui.js
```

These checks cover metadata privacy, hook merging, MCP under isolated Python, explicit health baselines, timestamps, worker restart isolation, native preview and all authenticated MASTER routes. They never request USB ownership or elevated setup. Physical changing-video cadence, sensor independence, actual Dot access, real mini capture, a continuous-use soak and installed-byte verification are distinct acceptance evidence. Use Windows extended paths or a short absolute test root for existing long-path fixtures.

## Unified library

`interlinked_hub.py` reads the unchanged client's `list` output in a bounded read-only subprocess. Refresh is asynchronous; offline failures retain an owned private catalog snapshot with its refresh time and stale status. The canonical shares remain `\\MSI\ProjectLibrary` and `\\MSI\ProjectReturns`. Exact general-catalog IDs already present in the reviewed MAGIX index route exclusively through MAGIX; other library IDs have opaque UI keys and retain their original ID for engine commands and release confirmation. Name/basename similarities never merge projects.

Favorites, recent projects and catalog snapshots live under owned `INTERLINKED-GUI-State/hub`, outside all managed `Files` directories. Status and storage observations do not verify current content. Last saved and verification events identify their scope/baseline; opening or editing afterward can invalidate content evidence. Generic Fetch, Save + Verify, Verify and exact-confirmation Release invoke the original sealed client with fixed arguments. Opening a working folder retains workflow and project-cache locks until the user closes all writers and explicitly finishes the session. An interrupted folder session requires reconciliation on restart.

The authenticated local API adds `POST /api/hub/refresh {}`, `POST /api/preferences` with either `{favorite,enabled}` or `{recent}`, and capability/readiness fields on `/api/catalog` and `/api/state`. Preferences accept known project identities only. Unsupported application launchers remain unavailable. There is no HTTP endpoint for arbitrary executable paths, shell commands, or global environment changes.

## MW5 guided transfers

`interlinked_mw5.py` supports only `mw5-companion` and the `LanceObserverDiagnosticControl` and `LanceObserverProbe` plugin trees. It inventories actual ordinary files, including recovery/configuration files; it does not silently exclude generated-looking folders. The project must first be fetched into its canonically owned READY cache. Editor discovery reads Epic installation metadata and validates the executable, project and plugin directory. If Epic registration is unavailable, an explicitly reviewed server-side `INTERLINKED-GUI-State/MW5-Editor-Reviewed.json` may pin the confirmed local installation and executable hash. The UI cannot supply paths or change this receipt. An editor update that changes the pin requires a new review.

Check editor, preview staging, then explicitly apply the preview. Existing different editor files are backed up before replacement; editor-only files remain preserved. Start the editor only after staging. Close it before previewing collection. Collection compares both sides to the session baseline and refuses conflicting independent changes. Previewed deletions preserve exact old bytes in the transaction backup. Applying collection finishes the local session only after the adapter confirms completion; Save to MSI + verify is a separate action.

The session holds the original project-cache lock and shared workflow lock. Previews bind exact source/destination content and cannot be applied after either side changes. Transaction journals, incoming payloads and independent prior-file backups live under owned `INTERLINKED-GUI-State/mw5`. Interrupted applies resume only their exact reviewed preview. A recovered COLLECTED journal requires explicit Finish MW5 session; it never implies save-back. Process checks, file/ancestor guards, space checks, stable reads, content hashes and readback guard transfers; no proprietary bytes are patched.

MW5 endpoints are `GET /api/mw5?project=<UI-ID>` and `POST /api/mw5/{check,preview,apply,finish}`. Check/finish take `{project}`; preview takes `{project,direction}`; apply takes `{project,previewId,confirmation}` with `STAGE` or `COLLECT`. Work is asynchronous and reports through the existing operation state. Cached readiness never substitutes for apply-time checks. Permission failures preserve journals/backups; no administrator launch or permission change is automatic.

New isolated suites: `test_hub.py`, `test_hub_integration.py`, `test_mw5.py` (each `--output <absolute-private-fixture-folder>`), and `node repository/app/test_hub_ui.js`. They cover discovery/offline behavior, adapter identity, preferences, native locks, conflicts, interrupted collection, low space, process/file refusal and HTTP authentication. The desktop demo contains four MAGIX families plus synthetic MW5 and coding projects. Its UI flow uses a labelled simulated adapter; separate native-file fixtures exercise the real transfer implementation.

Historical version browsing, self-contained archives, historical restore, remote job submission and automatic background synchronization are outside this release. Synthetic tests, installed checksums and actual editor acceptance are distinct evidence; a real MAGIX or MW5 editing/save-back test must be recorded separately.

## Read-only MSI jobs

The **MSI Jobs** view displays the last observed runner receipt, separate Task Scheduler state, exit code and bounded stdout/stderr tails. Opening the panel and polling its local API never connects to MSI. **Refresh from MSI** explicitly opens a separate native console for the existing MSI Windows password prompt. Enter the password only there. SSH stdin and stderr remain native; only the fixed status-query stdout is collected. No password is stored, and no SSH configuration, host setting or installed MSI file is changed.

The observer preserves the reviewed direct-cable endpoint, canonical `MSI` host alias, trusted Ed25519 public-key pin and remote account SID. Its fixed query reads only ordinary job records and literal log filenames under the existing MSI `ServerJobs/Jobs` root. Caller-supplied commands, remote paths and hosts are unavailable. It does not replay SSH verification fixtures, start/cancel jobs, release caches or import outputs. MSI must be awake and reachable. Missing scheduler data, mismatched receipts and interrupted/stale runner observations require review; successful SSH observation does not establish that a future job can run.

Refresh is independent of local editing sessions. It retains up to 50 jobs and 32 KiB per log stream, with list/log truncation identified. A failed refresh preserves the previous observation and marks it stale. Job snapshots, bounded log tails, ownership markers and per-refresh known-host files live under owned `INTERLINKED-GUI-State/jobs`, outside project data and Git. Closing INTERLINKED does not terminate any MSI job. The query helper has a bounded lifetime; native password prompting and actual two-PC status/log retrieval need separate acceptance on the deployed release.

The authenticated API is `GET /api/msi/jobs`, `GET /api/msi/jobs/log?job=<32-lowercase-hex-ID>&stream=stdout|stderr`, and `POST /api/msi/jobs/refresh {}`. GET reads only the local snapshot. Refresh accepts no parameters. There are no submission, cancellation, deletion or result-import endpoints.

Isolated checks, using a private fixture folder outside the installed kit/cache:

```powershell
python -B repository/app/test_jobs.py --output .handoff/jobs-tests
python -B repository/app/test_jobs_http.py --output .handoff/jobs-http-tests
node repository/app/test_jobs_ui.js
```

These cover synthetic remote receipts/logs, ownership/path/schema guards, stale/offline behavior, HTTP capabilities and origins, display escaping, and observation during a synthetic editor session. They do not authenticate to MSI or execute a real job.

### User-reported missing media

The compact **Reported missing media** panel accepts a filename/path and an optional multiline note during a MAGIX session. It never reads that path, watches dialogs, changes media, or retries a fetch. Audit-row flags derive their text from the selected project's reviewed reference. Reported missing, user-marked unavailable, and user-marked resolved are observations, not verified repairs.

Reports are stored outside all project caches in the owned `INTERLINKED-GUI-State/missing-media.events.jsonl`, with a separate ownership marker and lock. Each append is hash-chained, flushed, fsynced, and read back. Missing, torn, corrupt, or hardlinked ledger data fails closed and remains preserved. Exact case-normalized paths dedupe within one project; a filename-only report stays separate from a full path. JSON exports include original text and full project event history; CSV prefixes display cells with `Text: ` for spreadsheet use.

For a reviewed one-time observation import, load `MissingMediaLedger(state_path, exact_gui_owner_object, indexed_project_ids)` from `interlinked_gui.py` and call `.report(project_id, literal_path, request_id=<stable UUID>, source='user_dialog', note=<evidence/uncertainty>)`. This independent ledger lock does not acquire the GUI-instance, project or MAGIX workflow locks. Import only actual observations; do not propagate one popup to other projects, similar Unicode spellings, or sidecars.

`test_missing_media.py --output <fixture-folder>` uses synthetic local files only, including corrupt/torn history, hardlink rejection, UUID retries, active-session and shutdown ordering checks. `test_missing_panel.js <receipt.json>` uses a synthetic DOM. Existing `test_gui.py` covers authenticated report/export routes with fake children and a loopback fixture.

## Violet data reports

MASTER distinguishes self-reported Dot activity, delegated local-task observations, demos and unverified legacy reports. Reporter labels are not account authentication; an actual Violet roundtrip remains a separate acceptance gate. Reports expire after two minutes. The independently rendered avatar prioritizes evidenced failures, input requests and working activity, otherwise displaying a neutral unknown state with the source visible. MASTER queues up to 100 historical attention items for the next requested Violet check; reading does not acknowledge them, and acknowledgment never changes project health. No automatic Dot wake-up or continuous model polling is configured. See the MASTER plugin README for the four restricted tools and migration behavior.
