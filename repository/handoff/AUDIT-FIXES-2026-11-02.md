# Audit fixes and performance work — November 2, 2026

This source revision combines the reviewed AI/history workspace with the audit
fixes and the coordinated optimization work. Private state, model files, media,
installed applications and hardware configuration are outside this change.
The source checkout is not a runnable replacement for the installed kit.

## Correctness changes

- History exclusions now use a transactional local outbox, ordered rule intents,
  exact source/generation bindings and durable primary acknowledgments. Older
  replies cannot erase a newer choice. The primary fences delayed requests, and
  activation waits for resolved exclusions. Applied privacy revisions are
  separate from request-order reservations.
- Remote worker preparation and start retain their identities before dispatch.
  The node records the resulting import/task with the operation binding.
  Reconciliation observes existing work; cancellation leaves a durable fence
  against delayed starts. An uncertain operation does not replay inference or
  edits. Older nodes must be updated before new recoverable remote assignments.
- Display requests reserve their identity before worker startup. A late startup
  completion or failure cannot override a newer Disconnect, timeout or shutdown.
  Manual, automatic and recovery attempts use the same ordering checks.
- Model qualification binds the exact host, tag, digest, runtime, effective
  token budgets and reviewed policy. Same-digest aliases cannot inherit each
  other's evidence. Unknown policy differs from an explicitly absent policy.
  Historical evidence is retained but must be recalibrated for the new binding.
- Replica synchronization removes only its own redundant staging directories.
  Unmarked historical directories remain untouched. Existing destination bytes
  are verified before an incoming copy is considered redundant; conflicting
  retained data requires review rather than a false successful activation.
- Memory generation uses current primary eligibility plus immediate local
  privacy choices. Policy changes invalidate cursors, including exhausted ones.
  Legacy checkpoints preserve existing proposals and decisions. Uncertain
  inference requires an explicit retry, and new candidates bind exact model
  qualification without duplicating previously reviewed primary proposals.
- Package validation checks the GUI-to-launcher-to-Buddy dependency chain. The
  current explicit migration changes only the launcher’s Buddy hash and uses
  the verified preceding launcher/module pair. Historical provenance manifests
  and unrelated machine, path and runtime pins are preserved.

## Performance changes

| Area | Change |
| --- | --- |
| Interface refresh | Coalesce requests, wait for completion before scheduling again, pause hidden views, and preserve unchanged catalog selections. |
| Launcher output | Buffer writes while preserving prompt, end-of-file and periodic partial-output flushes. |
| Memory lists | Validate source references in batches and return bounded pages, including continuation through empty filtered pages. |
| Worker observation | Skip durable writes for unchanged observations; retain a separate observation time and meaningful detail revisions. |
| Model inventory | Cache advertised capabilities only for the exact tag, digest, runtime lifetime and policy. |
| Task lists | Return compact pages and fetch selected full details when their revision changes. |
| History indexing | Reuse verified unchanged vectors only when source, text chunking and embedding identity match. |
| Git observation | Run at most two independent background checks, bind results to their registered roots, and reject stale clean-state evidence. |
| Sensor sampling | Keep bounded latest-value acquisition outside the render loop, retaining identity checks and per-field expiry. |
| Native matte generation | Use bulk pixel access with equivalent alpha/color results across supported formats and signed strides. |

The isolated logging probe reduced 65,586 flushes to 17 while preserving exact
text and prompt handling. The native 86-key matte fixture measured median stage
time of 1,484.49 ms before and 18.66 ms after the change. These measurements do
not establish whole-application startup time, frame rate or physical display
performance. Synthetic vector checks cover 20 initial embeddings, zero for an
unchanged generation and one for a single changed source.

## Compatibility and delivery

Publish the matching application and native worker source as one reviewed
package. The current GUI seal has 26 members; full desktop publication has 40,
or 41 with explicit launcher migration. The companion node uses its exact
15-member v2 package and advertises additive recovery/history capabilities.
See [deployment notes](../deployment/README.md),
[AI operations](../app/AI-WORKSPACE.md) and [history operations](../app/HISTORY.md).

Existing calibration rows, uncertain worker operations, pending exclusions and
legacy memory attempts are retained conservatively. An application update does
not itself qualify a model or authorize a retry. Installation must retain the
existing ownership, session, exact-hash and stopped-process checks. Protected
startup bindings and sensor packages remain separately managed dependencies.

## Validation

The UI suites passed 381 checks; launcher logging and the existing GUI passed
57; native rendering, scheduling, helper cleanup and optimization fixtures passed
597. Full desktop publication passed 150 checks, cooler publication 202, and
stopped synthetic node migration 44. These checks do not invoke the real
publishers against installed software.

The AI suites verified 243 passing cases. Four optional node acceptance fixtures
were skipped because their separate TLS/DPAPI or sealed-runtime inputs were not
supplied. The new recovery suite passed 20 cases, including lost replies,
restarts, cancellation ordering and queued qualification changes.

The history store/service suites completed 39 and 35 tests, plus 21 repair
regressions and five final recovery checks. The integration pass covers history
privacy boundaries, isolated module bootstrap, scheduling, gateway behavior,
local IPC, HTTP validation, Buddy and frozen AI bridge requests. Expected
platform/native-fixture skips remain distinct from executed checks.

Independent barrier probes confirmed that an older remote query cannot overwrite
confirmed cancellation. Separate history probes verified delayed policy commits,
re-pair/restart ordering, damaged retained index/metadata/vector rejection, and
recovery-stage preservation without repeated downloads.

History tests use an isolated process profile when normal Windows file checks
reject the separately installed dependency bundle. The installed dependency
guard remains unchanged; these source results do not certify that bundle.
The HTTP oversized-request fixture sends an excessive declared length without
uploading the body, proving early rejection without depending on Windows TCP
behavior when a server closes with unread incoming data.

Validation uses isolated synthetic fixtures, local-only communication and fake
inference. No production archive was imported, model inference launched, MSI
node migrated, live cache edited, USB display connected or installed package
replaced for this source change. Installed two-PC operation, model quality and
human/physical display acceptance remain separate from the source checks.
