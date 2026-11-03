# Local history in INTERLINKED

INTERLINKED imports a ChatGPT export into a private archive owned by MSI and keeps a verified searchable copy on THIS-MACHINE. Ollama provides local inference and `embeddinggemma:300m` embeddings. The archive is separate from live INTERLINKED conversations and the Codex delegation plugin.

## Import and review

1. Open **History**, enter the extracted export folder or ZIP path, and choose **Import and verify**. An optional manifest SHA-256 pins the selected snapshot. Import reads the source without changing it, copies it into private intake outside cloud-synced folders, and verifies payload hashes.
2. Watch the import jobs and coverage report. Cancellation preserves the previously active generation. Repeating an unchanged export is idempotent; changed records become revisions. A missing record in a later export is retained rather than treated as a deletion.
3. Choose **Review passages and exclusions** on the prepared snapshot. Search and open passages before activation, including on the first import. The review label identifies the selected generation. Exclude unwanted sources here; handoff and standing-memory selection become available after activation.
4. Choose **Activate this reviewed snapshot**. MSI remains the authoritative writer, and the verified generation synchronizes to THIS-MACHINE.

**Open source** uses the citation's revision and character offsets. The viewer shows the exact passage, Unicode offsets and available page, slide, sheet/cell or Git-path references. **Show full source** opens the same revision. Unavailable revisions or invalid ranges produce an error rather than substituting different evidence.

## Coverage and limits

Coverage distinguishes archived, indexed, excluded, unsupported and missing material. Readable ordinary and multimodal conversation text is searchable; reasoning/recap records remain archival, and `chat.html` is not indexed as duplicate evidence. Parent links preserve conversation branches. Group-chat speakers remain unspecified when the export does not identify them.

Passive document adapters support PDF, DOCX, XLSX, PPTX, text/code, JSON, CSV, Markdown and HTML. Spreadsheet formulas and cached values are read without recalculation. Attachment file extensions, including `.dat`, do not establish their actual type or relationship; ambiguous and missing links remain explicit. Supported ZIP members are bounded during inspection.

Pages use current materialized text when its sequence agrees with the exported head. Stale projections are reported as incomplete coverage. Site bundles are read without checkout or execution; readable metadata, SQL and source blobs are searchable, and export omissions/redactions remain in the report. Account/service records supply linkage and coverage rather than conversation evidence.

Images, audio, video and unsupported formats are inventoried. OCR, transcription and visual interpretation are outside this release. Imported scripts, SQL, macros, formulas and external document relationships are never executed.

## Assistant and memories

In **Sync**, choose a local model and **Verify for history**. This checks the separate history profile: a 16,384-token context with a 4,096-token response allowance. It does not change other workflows' settings. Generations record the actual embedding model digest, dimensions and processing identity; the replica must match that identity for semantic search.

In **Assistant**, enable **History and reviewed memories** and choose a project scope. Current instructions and freshly checked project state take precedence over historical material. Turning history off or changing scope starts fresh server-retained inference context while preserving the visible transcript. Local conversations and API answers are not automatically imported into the archive.

In **Memories**, generate local suggestions or propose a memory from selected sources. Suggestions run in resumable batches of up to twenty sources, starting with identified user messages and then Pages. Continue generating to advance through the archive. Retrying a batch preserves its saved candidates and your review decisions; a changed source or model starts a separately bound run. Edit, approve or reject each proposal before activation. Every proposal retains its sources. A conversation marked “do not remember” remains searchable but cannot produce standing memory, including through derived or linked material. Unknown group-chat authorship cannot become an inferred personal preference. MSI owns memory approvals.

## Sync, offline use and exclusions

The app checks for updates at startup, after changes and every five minutes while running. Active MSI job counters refresh separately about every four seconds, with their observation time and stale status displayed. **Sync now** requests another archive check. Offline search uses the last verified local generation; the banner shows its date and last-sync time. Replication transfers verified immutable generation files, never a live SQLite database over SMB.

Exclusions take effect on the requesting machine, survive restart and reimport, and synchronize durably. They invalidate applicable search results, dependent memories, cached selections and retained inference context while preserving original source files. A disconnected machine receives exclusions it has not seen at its next successful sync. Review the **Excluded sources** list to include a source again; before first activation, select its prepared snapshot to restore it.

Exclusion updates have durable versions for each conversation, asset or individual
record rule. Pending and unresolved counts remain visible until MSI acknowledges
them. A delayed earlier response cannot reverse a newer choice. Prepared and active
sources use the same queue, bound to the exact reviewed generation, revision and
lineage; activation waits for pending review exclusions. An older node without
fenced-intent support leaves updates pending. Legacy pending files are preserved
as migration evidence; missing or conflicting source bindings require review.

Memory selection uses current MSI eligibility plus the requesting machine's own
exclusions. Restoring or retagging a source reopens the selection queue. The first
explicit Generate after the eligibility upgrade preserves the old checkpoint and
resets its cursor; saved candidates and existing proposal decisions are retained.
Candidate evidence binds the exact model tag, digest, runtime, context/response
budgets and reviewed policy. A changed qualification cannot relabel an old candidate.
An interrupted inference without a durable answer is reported for review and is
never automatically repeated. The local API accepts an explicit
`retryUncertainRequestIds` list on `memory-generate` only for individually reviewed
request identities; a normal Generate does not supply that authorization.

Proposal details are paged (up to 100 at a time); frequently refreshed state keeps
only its first page and counts. Effective statuses use one immutable generation
and batched source metadata validation. Memory generation also batches its initial
source validation while retaining a fresh check before each proposal submission.

Semantic imports reuse only unchanged chunk IDs and revisions from a verified
previous generation with the exact embedding identity, dimensions, normalization
and chunker format. New or changed chunks are embedded separately. Incompatible
or unverified previous vectors are rebuilt; interrupted builds retain verified
prefix checkpoints. Source restrictions and explicit activation remain unchanged.

Completed synchronization removes its owned redundant staging copy. Interrupted
owned stages can be cleaned safely after restart; unmarked historical directories
are preserved for review. If retained generation bytes are damaged, synchronization
keeps the verified incoming snapshot as recovery evidence and holds further
automatic synchronization for that generation. It does not replace the damaged
generation or discard recovery evidence automatically.

Private archive state is under `%LOCALAPPDATA%\INTERLINKED\History` on each host: MSI owns `archive`, and THIS-MACHINE owns `replica`. Intake, receipts, operation state, profiles and client credentials remain outside Git, cloud-synced folders and managed project caches. Owned archive/intake folders and local history-derived transcripts are excluded from ordinary project context exposed through the existing Codex bridge.

## Local client gateway

On THIS-MACHINE, open **Sync → Connect a local chat app**, choose its history/project profile, and create a client key. The key is displayed once; only its hash is persisted. Revoke a key in the same panel. Profiles are server-controlled: create a new key for a different profile and revoke the old one.

The endpoint is `http://127.0.0.1:47444/v1`. A port conflict is reported instead of taking over another listener. Keep INTERLINKED running. Requests use its existing model scheduling and capacity checks. Only advertised local models are accepted; cloud models, arbitrary upstream URLs and cloud fallback are unavailable.

List calibrated models from PowerShell using a placeholder key:

```powershell
curl.exe --header "Authorization: Bearer REPLACE_WITH_LOCAL_CLIENT_KEY" http://127.0.0.1:47444/v1/models
```

Save this as `request.json`, replacing the model with an ID returned by that request:

```json
{
  "model": "REPLACE_WITH_ADVERTISED_MODEL_ID",
  "messages": [{"role": "user", "content": "What did we decide about this project?"}],
  "max_tokens": 4096,
  "stream": false
}
```

```powershell
curl.exe --header "Authorization: Bearer REPLACE_WITH_LOCAL_CLIENT_KEY" --header "Content-Type: application/json" --data-binary "@request.json" http://127.0.0.1:47444/v1/chat/completions
```

Leave `max_tokens` unset or use exactly `4096`. To request SSE, set `stream` to `true` and add `--no-buffer` to curl. SSE sends keepalives while inference runs, then the completed buffered answer and completion marker; it does not stream generated tokens as they arrive. Answers include readable source references.

Only `GET /v1/models` and text-only `POST /v1/chat/completions` are supported. A request permits `model`, `messages`, `stream` and `max_tokens`; messages use plain string content and `system`, `user` or `assistant` roles. Tools, media, Responses/Embeddings routes, browser-origin requests and additional API parameters are rejected.

The gateway does not retain a client conversation. A client can resend old messages, including previous history-derived answers. History-off disables server retrieval of archive/memories; it cannot remove text that an external client supplies again. Start a fresh conversation in that client when changing its history/project profile. The app's fresh-context behavior applies to context retained by INTERLINKED itself.

## Handoff to ChatGPT or Codex

Select active-archive sources, open **Handoff**, and optionally add a note. Prepare and review the exact text, then choose **Copy**. Editing the note or selection invalidates the preview. Copying only writes the displayed text to the clipboard; it makes no cloud request and attaches no hidden files or metadata. Paste it into ChatGPT or Codex when desired. The existing delegation plugin does not automatically gain archive or memory access.

## Deployment and verification

History dependencies are packaged separately from the existing sealed Python runtime at `%LOCALAPPDATA%\INTERLINKED\HistoryRuntime\py312` or `py313`. The loader verifies the exact manifest and every file before adding this directory to Python's import path. The reviewed dependency set is NumPy 2.3.5 and pypdf 6.10.0. Manifest SHA-256 pins in `interlinked_history_runtime.py` are:

| Python | `DEPENDENCIES.json` SHA-256 |
| --- | --- |
| 3.12 | `e31b8ad42386acb5ff820efc6c9dc7ac6b8bc0c7b5ebbffaeed27e4c328d026e` |
| 3.13 | `95de595745770093e36a2db7b8b56ae4bbf18fc76f5e3a5eb4b915b1fbd28236` |

Update the distribution manifest and affected package seals for a reviewed delivery, then run `python -B repository/verify_source_snapshot.py` from the checkout. Component checks include `test_history_import.py`, `test_history_store.py`, `test_history_service.py`, `test_history_boundary.py`, `test_history_gateway.py` and `node repository/app/test_history_ui.js`. These use synthetic inputs; loopback fixtures can require normal Windows socket permissions.

Source integrity, synthetic component checks, installed-byte verification, native UI/clipboard checks, real two-PC operation and retrieval/answer quality are distinct evidence. Synthetic passing results alone do not establish deployment or model quality. Installed acceptance must verify both PCs, offline labels and sync behavior, and review at least 30 questions with retrieval coverage and generated-answer accuracy assessed separately.
