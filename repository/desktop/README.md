# INTERLINKED desktop application

The Windows application hosts the existing Matrix interface in Microsoft WebView2. It starts the reviewed Python service with `--desktop --no-browser`, receives the ephemeral loopback address through its private stdout pipe, and keeps the service running while its window is open. It uses the existing sealed Python runtime without installing Python packages or replacing the MAGIX launcher.

Production layout adds `INTERLINKED-Desktop/INTERLINKED.exe`, the isolated MASTER worker, the approved Violet atlas and sealed package companions beside the existing `INTERLINKED-App` folder. Run normally on THIS-MACHINE as Jude. The service verifies its original deployment path, exact fourteen-file v6 GUI seal (including MASTER, read-only MSI Jobs, Buddy and Stats), private project index and launcher hashes. The current GUI instance and MAGIX/cache locks remain authoritative.

With MASTER background mode enabled, closing the main window hides it in the tray. The tray's explicit **Exit INTERLINKED** requests authenticated `/api/quit`; busy operations and managed sessions still refuse shutdown. With background mode disabled, closing the window requests the same guarded exit. Save and close MAGIX, confirm Done, and wait for completion before exiting. Done does not save to MSI; saving, verification and exact-confirmation release retain their existing controls. Neither the host nor its test harness kills the backend or MAGIX processes. A renderer failure offers **Reopen interface**, which recreates the embedded interface while retaining the service and session.

The application uses an InPrivate WebView2 profile, disables host objects, web messaging, developer tools and browser context menus, and restricts navigation and resources to the exact current local origin. Media review opens a controlled application window. JSON/CSV reports use native Save dialogs. Camera, microphone, location and other browser permissions are denied; local multiple report downloads are allowed because every download is separately restricted to an expected local Blob report and explicitly saved. The capability token is never written to desktop receipts or logs.

The host has one instance per kit (or demo fixture). A second launch activates its existing window. The original backend lock also prevents competing browser/desktop services.

Window position, size and maximized state are remembered in `%LOCALAPPDATA%/INTERLINKED/window.json`, outside managed project files. The small owned JSON file contains no session capability. Bounds are constrained to a visible monitor; corrupt, differently owned, link/cloud or unavailable preference paths fall back to defaults without blocking work. Preference writes use a flushed temporary file and atomic replacement, only after the window actually closes following a safe service exit. A refused busy close writes nothing. Demo runs use only their separate fixture state's `window.json`.

## Build

Requires 64-bit Windows, .NET Framework 4.8, the built-in Framework C# compiler, Windows PowerShell and Microsoft WebView2 Evergreen Runtime. A .NET SDK is not required. The package contains the WebView2 SDK libraries and loader, not an entire browser runtime.

Download the official `Microsoft.Web.WebView2` **1.0.4314.35** NuGet archive from `https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4314.35/microsoft.web.webview2.1.0.4314.35.nupkg` into a private dependency folder as `webview2.nupkg`, then extract it into that folder's `webview2` subfolder. The pinned archive SHA-256 is `90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4`. The build checks this hash and every copied extracted member against the archive before compiling. Binaries, extracted dependencies, browser profiles, receipts and exports stay outside Git.

```powershell
.\repository\desktop\Build-Desktop.ps1 -DependencyRoot .\.handoff\desktop-build\deps\webview2 -OutputDirectory .\.handoff\desktop-build\package -VioletAtlas C:\private\approved-violet-atlas.png
```

Use an absent or empty output folder. `-VioletAtlas` is mandatory and must identify the approved transparent 1536 × 2288 v2 character atlas. The build compiles the desktop host and isolated worker, copies this private artwork as `Violet.png`, embeds a generated Matrix-style application icon and writes the strict ten-member package `DESKTOP-CHECKSUMS.json`. Preserve the included Microsoft license and notices. Artwork, compiled binaries and dependencies stay outside Git. Deploy only with the separate guarded publisher after checks pass and the production service/session locks are free.

## MASTER command deck

MASTER shows project activity separately from evidenced project health, with an exact 480 × 480 cooler-frame preview. Rendering and USB transfers run in `INTERLINKED.Master.Worker.exe`, outside the workflow-owning Python service. Its private input carries bounded JSON snapshots/settings; its output carries length-prefixed JPEGs. Status and sensor messages use its separate diagnostic pipe. A worker failure cannot complete, save, release or terminate a project operation.

The cooler is acquired only through **Connect display**. TRCC or another USB writer causes a visible conflict; MASTER does not close it or change TRCC startup. The worker uses the already installed vendor transport, verifies private helper-file pins, creates its image map exclusively and sends picture packets only. MASTER-created helpers are contained in owned Windows Job Objects. Borrowed helpers are never terminated. The optional vendor sensor reader reports absent, stale or unverified readings as unavailable; successful image delivery does not prove temperature independence.

**Enable live temperatures** is a separate explicit action after the display connects. It can show a Windows administrator permission prompt for the same sealed worker in its restricted sensor-host mode; the desktop app, project service and renderer remain unelevated. That mode can start only the pinned `C:\Program Files\TRCCCAP\HWINFO.exe` with its pinned `HWiNFO64.dll`, with no arbitrary command or path input. The parent holds its exact worker configuration and helper files against replacement while requesting permission. The broker verifies its parent and a private pipe restricted to the current Windows user, starts the sensor child suspended, assigns its owned Job Object, then resumes it. A closed pipe, lost parent or expired five-second lease stops its owned sensor child. TRCC and other sensor publishers are preserved and block a new privileged publisher. Canceling the Windows prompt leaves temperatures unavailable; display connection and startup never request elevation automatically.

The live-mini option requires user selection and calibration of the actual visible mini window over the black source area. Hidden, moved, covered, missing or non-black capture sources are unavailable rather than replaced with an idle character. Dot-reported updates remain distinct from measured project results. Local Codex hooks require the normal review/trust process; cloud Dot hooks and exact mini mirroring must be accepted separately in their real environment.

The reviewed installation can remember background mode and **Start at login**. The latter owns only the current user's exact `INTERLINKED MASTER` Run entry; a differing entry is preserved. Demo/source fixtures do not alter startup. After login the monitor can wait in the tray while TRCC retains the LCD. Explicit exit retains the existing session guards.

Standalone worker checks use a freshly built private worker and fixture directory:

```powershell
.\repository\desktop\Build-MasterWorker.ps1 -OutputDirectory .\.handoff\master-worker-build
python -B repository\desktop\test_master_worker.py --worker .\.handoff\master-worker-build\INTERLINKED.Master.Worker.exe --output .\.handoff\master-worker-check
```

These checks exercise changing encoded frames, protocol bounds, missing-mini reporting, refusal of sensor permission without LCD ownership, malformed broker arguments and EOF cleanup without USB ownership or an administrator prompt. `--self-test <fresh-private-folder>` on the worker adds packet geometry and pure argument-validation checks. These source/synthetic checks do not prove a privileged helper launched or that its lease cleanup works on hardware.

Window-source diagnostics use UTF-8 explicitly, including stderr. `python -B repository/desktop/test_master_encoding.py --worker <private-worker.exe> --output <fresh-private-folder>` creates its own Unicode-caption window, requests only `capture_sources`, and checks strict UTF-8 decoding and the exact owned caption. It discards unrelated window metadata, captures no pixels, and sends no USB or sensor commands. This catches Windows code-page regressions that otherwise leave the source selector empty.

The controlled capture fixture uses only its own synthetic Windows windows and never calls the worker's USB or elevation paths:

```powershell
python -B repository\desktop\test_master_capture.py --worker .\.handoff\master-worker-build\INTERLINKED.Master.Worker.exe --output .\.handoff\master-capture-check
```

Run the physical test only during a coordinated display test with TRCC closed. `--allow-usb` explicitly permits picture writes; `--enable-sensors` requests a real Windows permission prompt that the operator handles. The harness never accepts that prompt automatically. Default benchmarks target 5, 10 and 15 FPS; `--high-rates` adds diagnostic-only 20 and 30 FPS targets. Normal application choices remain 5, 10 and 15 FPS. The separate 30-minute soak selects 10 FPS when stable, otherwise 5 FPS.

```powershell
python -B repository\desktop\test_master_hardware.py --worker .\.handoff\desktop-build\package\INTERLINKED.Master.Worker.exe --violet .\.handoff\desktop-build\package\Violet.png --output .\.handoff\master-hardware-check --allow-usb --enable-sensors --high-rates --phase-seconds 60 --soak-seconds 1800
```

The receipt separates acknowledged pictures, encoded preview frames, worker CPU/memory and reported sensor state from physical-screen observation. Use `--motion-confirmed` only to record an already supplied human confirmation of motion. Acknowledgments alone do not establish visible motion, correct temperature labels, temperature independence, actual Dot access, real mini mirroring or recovery after reconnect. Those remain separate acceptance gates; synthetic or source-only checks cannot substitute for them.

`master_resource_probe.sample_owned(worker_pid)` supplies read-only Windows counters for the worker and its evidenced process descendants. It reads only PID/parent PID, executable basename, creation time, cumulative CPU time and working set. Denied or exited counters remain `null` with a reason. Compare CPU deltas per `(pid, createdAtTicks)`; summing cumulative totals across changing process trees can undercount exited helpers. Detached elevation-service processes are not guessed from matching names, so the sampler's explicit coverage note must remain with any resource summary.

## Isolated acceptance test

Read the application component README before any broader tests. This test uses four synthetic MAGIX families, one synthetic MW5 project, one synthetic coding/data project, a synthetic missing-media ledger and demo operations. It does not open MAGIX or the MW5 Editor, fetch media, inspect personal projects or contact MSI. The test needs a normal interactive Windows desktop; a restricted process sandbox may prevent WebView2 initialization.

```powershell
.\repository\desktop\Test-Desktop.ps1 -PackageDirectory .\.handoff\desktop-build\package -Python 'C:\Users\Jude\Documents\MSI-Project-Library\Project-Library-Client\Runtime\python.exe' -FixtureDirectory .\.handoff\desktop-build\acceptance
```

The real embedded browser checks the mixed catalog, project filters, favorite persistence, single-instance activation, blocked external navigation, the separate audit interface, JSON/CSV Blob downloads, busy shutdown refusal, interface recreation during an active demo session, generic folder-session completion, and the synthetic MW5 check/stage/open/collect/separate-save workflow. It also checks disabled native bridges, InPrivate mode, confirmed graceful service exit and isolated window preference ownership, bounds, roundtrip, corruption preservation, and save timing. Five additional checks exercise the MSI Jobs panel, read-only demo labels, stdout/stderr switching, explicit synthetic refresh and return to Library while preserving the active editor session. All 39 checks must pass. The fixture folder must be new and outside the production kit/cache. The test preserves processes on timeout. A passing demo does not certify actual MSI SSH authentication/status retrieval, MAGIX playback, every family's media dependencies, or a production MW5 asset/save/release cycle.

Official platform documentation: [WebView2 introduction](https://learn.microsoft.com/en-us/microsoft-edge/webview2/), [runtime distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).

## Violet data reports

MASTER distinguishes self-reported Dot activity, delegated local-task observations, demos and unverified legacy reports. Reporter labels are not account authentication; an actual Violet roundtrip remains a separate acceptance gate. Reports expire after two minutes. The independently rendered avatar prioritizes evidenced failures, input requests and working activity, otherwise displaying a neutral unknown state with the source visible. MASTER queues up to 100 historical attention items for the next requested Violet check; reading does not acknowledge them, and acknowledgment never changes project health. No automatic Dot wake-up or continuous model polling is configured. See the MASTER plugin README for the four restricted tools and migration behavior.

The separate `VIOLET:` line shows her reported `WORKING`, `WAITING`, `FINISHED` or `INTERRUPTED` state and its `LOCAL TASK` or `DOT REPORT` source, independently of other working projects. `FINISHED` means that turn ended, not that a project or test passed. A snapshot older than 15 seconds, report older than two minutes, disconnected source, missing observation time or unverified reporter displays `NO UPDATE` with its reason. Demonstrations explicitly display `DEMO · NOT LIVE`. Raw report messages and source strings are not rendered.

Run `Test-Violet-Status.ps1 -OutputDirectory <fresh-private-fixture> -VioletAtlas <approved-atlas>` for the focused native state/pixel checks. Its optional `-WorkerSource` selects the exact reviewed source for a status-only release. The fixture verifies that only Violet's readout changes while project rooms remain working, checks native-size text bounds, and uses no USB, sensor helper or live status publication. Physical acceptance still requires a genuine Violet report sequence and observation of the actual cooler; preview images and acknowledged frames remain separate evidence.
