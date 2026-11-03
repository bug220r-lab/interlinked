# INTERLINKED AI companion node

The companion uses a separately reviewed Python runtime and installed loopback
Ollama. It exposes only versioned AI RPCs over certificate-pinned TLS. It does
not download models, change Ollama's bind address, or run arbitrary shell text.
The desktop coordinator can run local inference directly; MSI uses this node.

## Package and initialize

Run `Build-Node.ps1 -OutputDirectory <new-private-package>` from reviewed source.
The output includes Python source, setup helpers and `NODE-CHECKSUMS.json`.
The current `interlinked-ai-node-package-v2` seal has exactly 15 members,
including the five history modules and the stopped-node migration helper.
Startup and initialization verify exact member sets for both historical v1
(nine members) and current v2 packages.
Record its SHA256 and the independently reviewed Python executable SHA256.
Deploy into an ordinary local folder under the intended user's profile.
Initialization requires .NET Framework 4.7.2+ and Windows PowerShell 5.1.

```powershell
.\Initialize-Node.ps1 -InstallDirectory <package> -ManifestSha256 <reviewed-hash> -Python <absolute-python.exe> -PythonSha256 <reviewed-hash> -BindAddress <MSI-direct-interface-address> -StartAtLogin
```

Initialization refuses existing state/configuration. It verifies payload hashes,
restricts the package ACL to the current user and SYSTEM, generates a private
3072-bit RSA TLS identity, and records computer/account/runtime identity. The
optional scheduled task uses the current user's limited interactive logon token;
MSI must remain signed in. It does not start or modify Ollama. Missing Ollama is
reported as unavailable. The runtime and its standard-library dependencies must
already be supplied and verified; the original sealed library runtime is never
modified by these helpers.

For the direct connection, run the separately reviewed `Set-NodeNetwork.ps1`
with `-InstallDirectory <package> -ClientAddress <THIS-MACHINE-direct-address>`
in an elevated Windows PowerShell session as the same user. It owns one Private
profile rule, scoped to the selected local address, remote address, port and
Python executable. `-WhatIf` previews it; `-Disable` disables only the matching
owned rule. It refuses a differing rule. Ordinary startup never requests elevation.

## Pair and run

Run `Start-Node.ps1 -InstallDirectory <package> -Pair` normally on MSI and enter
the displayed code, computer/SID identity, endpoint `https://MSI:47443` and
certificate SHA256 in INTERLINKED's pairing form. Verify the fingerprint from
MSI's actual display. Pairing codes expire after five minutes, allow at most five
attempts, and are consumed once. The client checks the TLS certificate before
sending any code, credential, or project content. Tokens are saved with DPAPI
for the current Windows user on each host; private TLS keys inherit the owned
package ACL. Re-pairing replaces the old token. The launcher keeps an exclusive
instance lock. Existing startup instances must exit normally before manual pairing.

`Start-Node.ps1 -InstallDirectory <package> -Revoke` revokes the retained token
while the node is stopped. Normal `Start-Node.ps1` runs without showing secrets.
No token, request body, raw prompt or response is written into HTTP logs.
Task journals and results remain in owned private state. A repeated completed
RPC ID returns its existing result; interrupted/uncertain RPCs are not replayed.

Nodes with `workerOperationRecoveryV1` correlate worker prepare/start RPCs with
their effects in the same durable workspace-state write. Authenticated
`operation_query` accepts targetRpcId, expectedAction and expectedFingerprint;
it observes the existing import/task without replaying its action.
`operation_cancel` accepts that same binding and durably fences future dispatch,
including an original request delayed in transit. It reports cancel_pending
until a registered worker is terminal. Identity mismatch or unavailable state
requires recovery review. These additive operations retain the existing protocol
identifier; clients require this capability before new remote worker transfers.
New workspace operation receipts make an older writer refuse the changed state;
do not downgrade over recovery records or remove tombstones to bypass a guard.

The launcher checks every package member and the reviewed interpreter before
starting. Package replacement, key rotation and startup removal require a
reviewed migration; these scripts deliberately preserve existing installations.

## Upgrade an existing node

Prepare and review a new v2 package in a separate ordinary local directory.
Preserve the installed package's manifest hash, `node.json` identity, interpreter
pin and inference-policy pin. The existing node must exit normally first;
`Migrate-Node.ps1` refuses either a held launcher lock or a direct Python node
lock. It never stops or restarts a process, scheduled task or model runtime.
Run the candidate helper as the installed user without elevation:

```powershell
.\Migrate-Node.ps1 -InstallDirectory <existing-node> -CandidateDirectory <reviewed-v2-package> -ExpectedBeforeManifestSha256 <installed-hash> -ExpectedAfterManifestSha256 <candidate-hash> -ReceiptDirectory <separate-private-receipt-directory>
```

This defaults to preflight. Add `-Apply` only to the reviewed invocation to
publish the exact pinned package. The helper makes durable, hash-verified
backups, verifies each replacement, and updates `node.json`'s manifest pin last.
It retains TLS keys, pairing and job state, reviewed checks, runtime identity,
network/startup settings and inference policy. A write failure restores owned
changes when their bytes still match and records either `FAILED_ROLLED_BACK`
or `FAILED_RECOVERY_REQUIRED`; inspect the receipt before any restart. An
interrupted process leaves the original backups and an incomplete receipt.
Do not initialize over an existing node or bypass either instance lock.

## Private history dependencies and transport

History uses separately sealed NumPy 2.3.5 and pypdf 6.10.0 packages under
`%LOCALAPPDATA%\INTERLINKED\HistoryRuntime\py312` or `py313`, matching the
reviewed interpreter. Supply the reviewed `DEPENDENCIES.json` and every listed
file before enabling history. The loader pins that manifest, verifies exact
inventory and file hashes, and refuses modules loaded from other locations.
These packages do not modify the installed Python runtime. Isolated PDF
workers independently load the same seal and enforce time, memory and output
limits. A missing or mismatched runtime is an explicit error.

MSI is the sole archive and memory-approval writer. The paired TLS transport
accepts bounded, hash-verified intake chunks and transfers immutable searchable
generations to THIS-MACHINE. Private archive state lives outside OneDrive,
source checkouts and managed project caches. Never put a live SQLite database
on SMB: a replica verifies and atomically activates a complete generation,
retaining its previous usable generation if transfer is interrupted. Offline
search displays the retained generation and last synchronization time.

## Remote workers and checks

The node receives verified text snapshots into its own state. Worker tools never
accept a client-supplied absolute path. It returns proposed changes and evidence
to the coordinator; applying to an active project remains a local explicit action.
Model requests use a pull mailbox, so inference can run on either machine while
file operations and checks stay on the selected execution host. No reverse
network connection or new listening port on THIS-MACHINE is required.

Check command authority stays on the execution host. Optionally maintain an
owned `reviewed-checks.json` beside `node.json`, keyed by the coordinator's exact
source project ID. Each value is an array of reviewed profiles:

```json
{"project-id":[{"id":"unit","argv":["C:\\reviewed\\python.exe","-B","tests.py"],"timeoutSeconds":120}]}
```

The node registers these profiles for the imported snapshot and records the
executable fingerprint. Remote assignments can select existing profile IDs but
cannot register commands. Without a matching node-local profile, check requests
are refused. Checks use the existing contained Windows process-tree helper;
ordinary working copies and Job Objects are not an OS security sandbox.

## Validation

`python -B repository/app/test_ai_node.py` exercises state ownership, pairing,
expiry, revocation, credential wrapping, idempotency, uncertain-call refusal,
certificate pinning, loopback restriction, local/cloud model selection, audio
wire shape, cancellation and worker action restrictions with isolated fixtures.
These unit tests do not establish MSI deployment, firewall access, model quality,
microphone input, audio inference reliability or actual local/remote performance.
Those remain distinct live acceptance checks after reviewed installation.

## Cooler hardware telemetry

The authenticated, certificate-pinned `telemetry` RPC accepts an empty payload
and reads a separate hardware cache. One sampler runs every two seconds without
overlap; it never requests model inventory, inference, loading or calibration.
NVIDIA GPU zero is identified by its UUID and supplies load, VRAM and GPU
temperature. Each field expires independently after five seconds; missing data
is unavailable rather than zero. Older nodes without this additive RPC remain
unavailable to the cooler resource display until separately reviewed migration.

MSI CPU temperature comes only from the separately reviewed MSI sensor publisher
at `C:\ProgramData\INTERLINKED\MSI-Sensors`. The reader checks its fixed
descriptor/output paths, exact MSI/account identity, protected Admin/SYSTEM
permissions and publisher executable SHA256. Sensor setup is explicit and
separate; neither node polling nor cooler reads starts a helper or requests
elevation. THIS-MACHINE retains its existing verified sensor-map publisher.
The desktop's `cooler_resources()` summary is private display data and is not
added to generic AI observations or plugin exports.

Native text chat requests disable automatic prompt truncation. Compatible audio
requests use `/v1/chat/completions`, which does not expose the native `num_ctx`
and `truncate` controls. Its effective context is the runtime's setting; report
the loaded model context from `/api/ps` and bound input rather than claiming the
requested native context was applied. Audio availability requires repeated
actual fixtures for each model/runtime, separately for speech and other sounds.

## Sealed desktop runtime TLS dependency

The existing sealed Python 3.12 runtime has a custom SSL trust hook whose CA
file resolves beneath `python312.zip`; constructing a TLS context currently
raises `FileNotFoundError`. Preserve that runtime and its trust hook. Configure
the separately installed standard Python runtime as a pinned TLS helper:

```powershell
.\Set-TransportRuntime.ps1 -AIStateDirectory <owned-GUI-State/ai> -Python <reviewed-python.exe> -PythonSha256 <reviewed-hash>
```

The coordinator verifies computer/account identity and executable hash, then
passes only the bounded TLS request through an owned subprocess's private pipes.
Tokens and prompts never enter command-line arguments. The helper checks the
same certificate pin and node identity; it does not weaken TLS verification.
Cancellation terminates only this owned helper. This is a separately installed
dependency, not a modification or replacement of the sealed library runtime.

## Pinned inference policy

An optional node-local `inference-policy.json` can disable thinking for an exact
installed model tag and digest. This supports short worker requests on MSI when
the model's default reasoning consumes the request deadline. It changes neither
the model files nor Ollama's global settings. The owned `node.json` must contain
the reviewed file's SHA256 as `inferencePolicySha256`; the launcher verifies it
and passes the path and hash to the node. RPC clients cannot set this policy.

The JSON schema is an object with `format` set to
`interlinked-ai-inference-policy-v1`, `identity` containing the exact computer
and SID, and a bounded `models` array. Each entry contains only `model`, the
exact 64-character `digest`, and boolean `think`. A configured tag whose digest
changes is refused before inference. Other model tags retain their defaults.
Inventory advertises the policy hash separately from the actual Ollama version
and actual model digests.

First enable requires verifying that the affected model has no retained passing
coordinator calibration, then running fresh capability checks. The node retains
`State/inference-policy-pin.json`; later starts refuse a changed or removed
policy even if the launch configuration was repinned. Policy migration is not
automatic: it requires a separately reviewed migration and coordinator
calibration reset/recalibration. Do not remove this state pin to bypass the guard.
