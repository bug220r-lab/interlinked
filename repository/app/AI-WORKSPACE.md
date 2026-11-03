# INTERLINKED AI workspace

The AI workspace joins Assistant, Workers and Models in the existing authenticated
desktop service. It operates on explicitly approved project context and installed
local models. Model downloads and runtime upgrades remain separate operations.

## Prompt-aware Auto routing (candidate)

Assistant, workers, bridge analysis and Buddy share `routingMode`: `balanced`
(default), `fast`, or `best_quality`. Exact model overrides bypass classification
and retain capability, digest, host and context guards. Auto classifies each new
user turn or worker assignment with a separately qualified local profile; tool
continuations and worker steps retain their assignment. Worker reviewers use a
distinct digest and the highest qualified quality.

Fast selects the shortest estimated completion among profiles scoring at least
90/100 and passing critical checks. Balanced uses the same floor and stays within
three points of the highest quality. Best quality ranks quality before expected
completion. Queue, observed residency, measured loading and input size contribute
to estimates. Small capability fixtures remain a labelled limited-evidence fallback
when task profiles are missing; they do not qualify a model for a project role.

Private `ai/routing-evidence.json` uses `interlinked-routing-evidence-v2` with
`profiles` and `classifiers` lists. Profiles bind host, digest, runtime, context,
response budget, reviewed policy hash (explicit null means no policy), fixture and
grader versions. Only adjudicated development results belong in this registry;
benchmark export creates a candidate and never installs evidence automatically.
Missing, invalid, ambiguous or mismatched evidence is not treated as qualification.
Qualification also binds the exact inventory tag and the effective input/output
budgets. Same-digest aliases do not share trials or task/classifier profiles.
Capability calibration v3 records the full `qualificationFingerprint`; older
receipts remain historical and display recalibration required. A missing node
policy observation is unknown, distinct from an explicit null policy hash.
History uses its fixed 16384/4096 profile budgets for this identity.

The `routing_v1` node profile is text-only, tool-free and schema-constrained, with
512 output tokens and a 30-second classification deadline. It preserves reviewed
thinking policy. Older nodes can answer ordinary requests but cannot classify.
Failed classification visibly falls back; cancellation, changed settings and
expired request deadlines stop. Durable classifier receipts precede dispatch;
restarted or duplicate operations never repeat uncertain inference automatically.

`classify_prompt` is a bounded supplied-text bridge evaluation operation requiring
an exact host/model with text calibration and node profile support. It does not
qualify the classifier itself. `ai_routing_benchmark.py` freezes and validates the
private 30-prompt corpus, journals bounded calls, reports missing human/executable
grading, and uses only the installed coordinator. New source support is separate
from installed protocol support and live model-quality acceptance.

Auto worker selection returns a durable pending task identity before inference.
Buddy v2 Auto receipts permit a pending model; v1 manual histories remain readable.
Routing and runtime timings are separate metadata, never added to tool messages.
No first-token timing is claimed by these non-streaming interfaces.

## First use

1. Open Models and refresh THIS-MACHINE. An installed/advertised capability is
   not a verified capability. Run Text, Coding and Tools tests for worker models;
   test image/audio fixtures separately using a known expected answer fragment.
2. Register projects in MASTER, then approve and index their context in Assistant.
   Search uses SQLite FTS5, bounded text, exclusions and current file fingerprints.
   Changes or unavailable files are labelled stale. Approval records bind the root.
3. Assign work to one project. Choose permissions, execution host and budget.
   Model selection prioritizes fixture accuracy among eligible models, with latency
   as the tie-breaker. These small fixtures measure limited tasks, not general model
   intelligence. Context/runtime/model changes invalidate their routing evidence.
4. Review the retained diff, checks and independent model review. Apply requires the
   exact result fingerprint and unchanged source baseline. Collection acquires the
   existing workflow/cache locks. Saving to MSI remains the existing separate action.

The second reviewer must have a different model digest. If unavailable the result
stays review pending. Check commands execute only from profiles reviewed on their
execution host. The working copy and Windows Job Object contain edits and process
lifetime; they are not a security sandbox for arbitrary hostile programs.

Existing models loaded outside INTERLINKED cannot be unloaded by it. Unload of an
INTERLINKED-loaded model additionally requires confirming other clients are idle.
Only one INTERLINKED inference runs per host. The coordinator queues for capacity,
retains failures, and never retries uncertain worker actions automatically.

Thinking models share the response-token budget between reasoning and final
content. A completed Ollama response can exhaust that budget with no final
answer. For the measured Gemma 12B Buddy request, 2048 tokens ended with
`done_reason=length` and empty content; 4096 produced a final answer. This is
task-specific evidence, not a guarantee for every prompt. Increasing response
tokens reduces the conservative input allowance at the same context size.
The bridge rejects empty/whitespace-only analysis as `empty_analysis_response`
without exposing reasoning or silently resubmitting the request. This source
guard requires reviewed publication before it applies to an older installation.

## Private state and transport

The owned `INTERLINKED-GUI-State/ai` folder holds configuration history, calibration,
attachments, conversations, context index, task requests and worker evidence.
It is excluded from project context and source distribution. Requests carry the
existing per-run capability token and same-origin checks. `/api/ai/` routes cover
state, refresh, pair, settings, search/context approval, attachments, calibration,
model load/unload, chat/cancel, task start/cancel/apply, and bounded event reads.

MSI uses the separately packaged [companion node](../ai-node/README.md), pinned TLS,
computer/SID identity and per-user protected credentials. Worker snapshots and
results are verified text bundles. A pull mailbox permits the worker to execute
on MSI while inference executes on either computer without a reverse connection.

The existing sealed portable Python has a TLS initialization path issue on this
installation. The optional server-only `transport-runtime.json` selects a separately
reviewed installed Python by identity and executable hash. The node transport helper
then performs TLS in that interpreter. Secrets travel over private pipes, and
certificate pinning is unchanged. The sealed client runtime is never patched.

Local check profiles can be registered with the `Workspaces.register_check`
administrative API while the application is stopped. It records the reviewed
executable hash and exact argv for the approved project. This API is not exposed
to model tools or HTTP. MSI's owned `reviewed-checks.json` is documented in the node
guide. Project commands must be reviewed with the same care as running them manually.

## Voice and acceptance

PNG/JPEG images and WAV audio are bounded to 12 MiB each. Push-to-talk defaults to
speech; uploaded audio defaults to general understanding. Only tested capabilities
are routed. Installed Windows voices supply optional spoken replies through a
restricted native bridge. See [voice acceptance](AI-VOICE-ACCEPTANCE.md).

Run `test_ai.py`, `test_ai_http.py`, `test_ai_node.py`, `test_ai_workspaces.py`,
`test_ai_integration.py` and `node test_ai_ui.js` from this directory using an
appropriate Windows runtime. Network/file-lock fixtures require normal Windows
permissions. Every suite uses isolated state; real MSI pairing, model quality,
microphone capture and audible output remain separate acceptance evidence.

The v9 GUI seal contains twenty-one exact source/web files. Publication accepts
historical v1–v8 baselines, retains backups, and requires inactive managed sessions
and free installation locks. A changed desktop/backend also requires separate
validation of any existing app-hash-bound cooler startup opt-in.

## Violet local access

The separate INTERLINKED AI plugin calls the existing coordinator through a
same-user Windows named pipe. See [plugin operation and acceptance](../ai-plugin/README.md).
It exposes bounded analysis, approved-context search, worker assignments, model
calibration and load requests, revision-checked settings, and retained job results.
It cannot apply results, approve new roots, pair hosts or register check commands.
MASTER keeps its existing four status tools.

Violet may manage models within assigned work. Permanent settings change only
when requested. Context defaults to all already-approved projects; each bridge
job records its scope and can instead use selected projects or supplied text only.
Bridge jobs retain request identities and do not replay uncertain work after a
restart. A local response or synthetic task does not establish actual Dot access.

## Remote worker recovery and state reads

New MSI assignments require the advertised `workerOperationRecoveryV1` node
capability before transferring source. Each prepare/start RPC identity and exact
payload fingerprint is durable before dispatch; node-side effect registration
is atomic with its correlation receipt. A lost response remains recovery required.
Explicit Reconcile only queries the original effect; it does not resume a worker,
replay prepare/start, or service inference mailboxes. Cancel persists a node-side
tombstone that also fences a delayed original request. Cancellation remains
unconfirmed while any original task is active or the node cannot be reached.
Older nodes retain ordinary capabilities; uncertain older assignments require
manual review. Existing state is never automatically replayed after restart.

`GET /api/ai/state` returns at most fifty compact task summaries plus `tasksPage`
with total and nextCursor. `GET /api/ai/tasks?limit=50&after=<id>` pages summaries;
`GET /api/ai/task?id=<id>` retains full selected-task evidence. Summaries include
revision, resultAvailable, and recoveryAvailable. Authenticated
`POST /api/ai/tasks/reconcile {"id":"..."}` observes existing remote work;
the existing cancel route also handles uncertain remote assignments.
Unchanged remote observations update an ephemeral last-observed time without
rewriting retained task history. Dispatch, cancellation, result, and terminal
state transitions keep their durable writes. Advertised capability discovery is
cached only under the exact tag/digest, listener process lifetime, runtime
version, and reviewed policy hash; tags, residency, capacity and dispatch
identities are still read afresh.

`test_ai_recovery.py` covers dropped responses, durable cancellation, restart,
exact qualification, compact paging and bounded unchanged-write behavior using
synthetic state and in-process transports.

An unload request appears in Models under Violet model requests. The user confirms
that other Ollama clients are idle before the exact owned model can be unloaded.
The plugin cannot supply that confirmation. Model, runtime or ownership changes
invalidate the pending request. Running inference retains its settings; a changed
settings revision stops queued inference or the next worker/calibration step.
