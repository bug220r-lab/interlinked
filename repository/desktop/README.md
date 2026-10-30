# INTERLINKED desktop application

The Windows application hosts the existing Matrix interface in Microsoft WebView2. It starts the reviewed Python service with `--desktop --no-browser`, receives the ephemeral loopback address through its private stdout pipe, and keeps the service running while its window is open. It uses the existing sealed Python runtime without installing Python packages or replacing the MAGIX launcher.

Production layout adds `INTERLINKED-Desktop/INTERLINKED.exe`, the isolated MASTER worker, the approved Violet atlas and sealed package companions beside the existing `INTERLINKED-App` folder. Run normally on THIS-MACHINE as Jude. The service verifies its original deployment path, exact fifteen-file v7 GUI seal (including MASTER, MSI Jobs, Buddy, Stats and configuration recovery), private project index and launcher hashes. The current GUI instance and MAGIX/cache locks remain authoritative.

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

The cooler uses the reviewed Matrix layout: the original approved Violet atlas is centered above three stations, each with one aggregate activity figure. Status-colored binary streams surround active wires and figures; stale/unknown stations have no active flow. The compact detail strip follows the focused project, prioritizing current check failures before input requests. Additional registered projects page in groups of three around that focus. Chat turn completion is labeled separately from build/test check results. The real attention queue supplies the review count; missing observations remain unavailable. Temperatures use fresh verified independent readings, with adjacent large CPU/GPU labels and values. A snapshot older than fifteen seconds neutralizes project status and hides temperature values.

The renderer also shows observed working subagents: a total below the source and alert text to Violet's right, and a compact `SUBS` count above each project's existing figure. `N?` means N confirmed working with incomplete tracking; `—` means unavailable, not zero. The total covers all registered projects, including later pages. Counts do not change activity, build/test evidence, attention priority or the approved artwork. A stale whole snapshot hides all counts. The desktop MASTER panel shows the same summaries and explains their source under **Source & evidence**. This addition requires a newly sealed app/worker publication; source fixtures do not update the installed display.

For a renderer-only update, `../deployment/Publish-Interlinked-Cooler.ps1` verifies the existing installed baseline and a complete reviewed baseline plan: 26 files for v5, 28 for v6 or 29 for v7, but permits changes only to the worker executable and its matching desktop seal. It preserves the GUI version, approved atlas, launchers and dependencies, retains exact backups, and reuses the full publisher's session/process/ownership guards, including Stats. It never bypasses a held lock or migrates the GUI. The broader desktop publisher remains responsible for GUI/configuration releases.

Native render checks compile only a synthetic fixture entry point and never run the worker's USB, capture or sensor setup paths:

```powershell
.\repository\desktop\Test-Master-Render.ps1 -OutputDirectory .\.handoff\cooler-render-check-new -VioletAtlas C:\private\approved-violet-atlas.png
```

The fixture checks status/evidence priority, workspace freshness, turn completion, sensor validity, focused-project paging, changing rendered pixels without diagnostic animation, and inactive stale stations. The optional artwork remains outside Git; the receipt records its SHA-256. Inspect the generated PNGs for layout. Synthetic checks do not certify physical LCD appearance, delivered cadence or real sensor availability.

Display ownership is requested by **Connect display** or the separately configured, hash-bound automatic-startup opt-in described below. TRCC or another USB writer causes a visible conflict; MASTER does not close it or change TRCC startup through its display controls. The worker uses the already installed vendor transport, verifies private helper-file pins, creates its image map exclusively and sends picture packets only. MASTER-created helpers are contained in owned Windows Job Objects and have separate input pipes so they cannot consume worker commands. Borrowed helpers are never terminated. The optional vendor sensor reader reports absent, stale or unverified readings as unavailable; successful image delivery does not prove temperature independence.

The source renderer now supports normal targets of 5, 10, 15 and 30 FPS, with 30 as the default for new preferences. Existing saved lower targets remain valid. Frames use GDI+ JPEG quality 100. Carried monotonic deadlines skip missed slots instead of queuing catch-up frames; a separate USB frame-ready notification wakes the sender without consuming the preview notification. Both consumers retain the latest immutable encoded frame. Cached-picture repeats remain limited to once per second, and more than three seconds without new production stops the stream. Quality 100 remains lossy JPEG; the measured encoder uses 4:2:0 chroma sampling.

The private baseline prototype sustained 28.69–28.98 helper-consumed FPS at a 30 FPS target over three one-minute Q100 trials. A later merged candidate completed the user-selected five-minute Q100/30 soak at 29.33 helper-consumed FPS over 300.53 seconds, with zero skipped frames and no errors. Its one-minute standalone trial delivered 29.52 FPS. The actual MASTER service and authenticated HTTP preview delivered 29.49 FPS before elevated sensor permission and 29.37 FPS with fresh CPU/GPU readings; sensor disable, explicit disconnect and owned worker shutdown completed cleanly.

At the user's request, an observed repeat kept authenticated HTTP preview and permitted live sensors active for a full 300.52 seconds, delivering 29.29 helper-consumed FPS. The user confirmed clear artwork, labels, changing diagnostic motion and separate updating CPU/GPU numbers without flicker or corruption. The quiet synthetic project registrations had fixed statuses. Stopping sensors made their values unavailable while picture delivery continued, followed by clean disconnect and exit.

These are helper-consumption measurements from production counters sampled about every two seconds, not optical FPS or physical panel refresh. The HTTP test does not include WebView2 image decoding. Human LCD appearance observations, per-frame timing, independent temperature accuracy and longer-term stability remain distinct evidence. A five-minute soak does not establish thirty-minute stability. Production installation is not performed by a source build.

Scheduling and notification checks compile a synthetic entry point without USB or sensor access:

```powershell
.\repository\desktop\Test-Master-Scheduling.ps1 -OutputDirectory .\.handoff\master-scheduling-check-new
```

**Enable live temperatures** is a separate explicit action after the display connects. It can show a Windows administrator permission prompt for the same sealed worker in its restricted sensor-host mode; the desktop app, project service and renderer remain unelevated. That mode can start only the pinned `C:\Program Files\TRCCCAP\HWINFO.exe` with its pinned `HWiNFO64.dll`, with no arbitrary command or path input. The parent holds its exact worker configuration and helper files against replacement while requesting permission. The broker verifies its parent and a private pipe restricted to the current Windows user, starts the sensor child suspended, assigns its owned Job Object, then resumes it. A closed pipe, lost parent or expired five-second lease stops its owned sensor child. TRCC and other sensor publishers are preserved and block a new privileged publisher. Canceling the Windows prompt leaves temperatures unavailable; display connection and startup never request elevation automatically.

The live-mini option requires user selection and calibration of the actual visible mini window over the black source area. Hidden, moved, covered, missing or non-black capture sources are unavailable rather than replaced with an idle character. Dot-reported updates remain distinct from measured project results. Local Codex hooks require the normal review/trust process; cloud Dot hooks and exact mini mirroring must be accepted separately in their real environment.

The reviewed installation can remember background mode and **Start at login**. The latter owns only the current user's exact `INTERLINKED MASTER` Run entry; a differing entry is preserved. Demo/source fixtures do not alter startup. After login the monitor can wait in the tray while TRCC retains the LCD. Explicit exit retains the existing session guards.

### Optional automatic cooler startup

The separately reviewed `Build-CoolerStartup.ps1` package adds one-time administrator setup for THIS-MACHINE/Jude. It embeds a fixed-function temperature publisher and the reviewed vendor sensor hashes. Setup installs it under `C:\Program Files\INTERLINKED Cooler Startup`, with only SYSTEM/Administrators allowed to write, and creates the account's interactive logon task `INTERLINKED Cooler Temperatures`. The task elevates only this publisher, stores no password, accepts no command arguments, and never launches the main app as administrator. It creates the vendor sensor map and starts only the pinned Program Files HWINFO executable, suspended until assigned to its owned kill-on-close Job Object. Existing TRCC/HWINFO processes are preserved. The task retries failures at most three times, one minute apart. The publisher remains available until sign-out even if the main app closes.

Setup enables automatic display acquisition only after its task readback succeeds. The protected `startup.json` records explicit opt-in and exact app/worker hashes. The normal service requires valid, known Start-at-login preferences, the exact production location and matching hashes. It waits up to sixty seconds for sensor initialization, then gives USB its own sixty-second bounded retry interval within a 150-second overall startup bound. Any manual display action cancels remaining startup attempts; disabling startup, unavailable configuration, an external unapplied configuration revision or removal of the setup record also cancels them. No capture is resumed. A later app/worker change invalidates that opt-in. Rebuilding alone does not replace an existing installation: setup deliberately refuses an existing task or retained installation folder, including after disable. Replacement requires a separately reviewed procedure.

Build with the verified account SID and a fresh private output folder, then run `Test-CoolerStartup.ps1 -BuildDirectory <folder>` as a normal Windows user. Refusal/resource fixtures do not install tasks, request elevation or start hardware. Publication still uses the existing full app publisher and refuses active Stats/MAGIX/MW5 sessions. After that publication, the user runs the frozen `INTERLINKED.Cooler.Startup.Setup.exe` and handles its Windows administrator prompt. The installed setup is retained in the Program Files startup folder; its explicit `--disable` action verifies task ownership, archives the opt-in with a unique name before disabling/stopping its task, and retains installed files. Ordinary installation does not stop any existing manual sensor publisher.

Optional setup also manages the exact reviewed `TRCCAppStartup` task so the two logon publishers do not compete. The build checks its fixed TRCC executable, arguments, principal and logon trigger, then embeds the original XML hash. Setup saves a protected copy before any change and validates that disabling changes only its enabled state. It registers the INTERLINKED task disabled and writes the display opt-in only after successful readback. Caught setup failures trigger rollback. An interrupted setup can be recovered through the retained setup executable’s explicit `--disable` action, even if no live opt-in was committed. Restoration requires proof of TRCC’s original enabled state and unchanged reviewed definition. Missing or independently changed tasks are preserved for review. Existing running TRCC/HWINFO processes are never stopped.

Source checks, native refusal checks, installed task/ACL readback, an app restart, and an actual PC restart/sign-in are separate acceptance steps. A staged setup or passing synthetic test does not establish automatic startup after reboot.

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

Run the physical test only during a coordinated display test with TRCC closed. `--allow-usb` explicitly permits picture writes; `--enable-sensors` requests a real Windows permission prompt that the operator handles. The harness never accepts that prompt automatically. The existing hardware regression harness benchmarks 5, 10 and 15 FPS by default; `--high-rates` also includes diagnostic 20 and normal 30 FPS targets. Its sparse statistics and historical soak selection are regression evidence, not the precise per-frame qualification used for the 30 FPS candidate. A final candidate soak must explicitly measure the selected 30 FPS condition and helper resources.

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

The tray applies startup and background preferences only when MASTER reports both valid settings and known lifecycle choices. Unavailable or recovered unknown settings never supply actionable defaults. Recovery keeps trusted current lifecycle choices and never replays historical startup settings. `Test-MasterTray.ps1` injects a simulated startup writer; its fixtures do not change the real registry.
