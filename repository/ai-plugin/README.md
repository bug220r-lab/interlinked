# INTERLINKED AI local plugin

This separate desktop-only stdio MCP plugin connects to the single running
INTERLINKED AI coordinator through a user-only Windows named pipe. It never starts
a second coordinator, reads the GUI token, or exposes raw Ollama publicly.
MASTER's four tools and package remain separate.

## Prepare and connect

First publish and verify the reviewed application containing `ai_bridge.py`,
`interlinked_ai_ipc.py` and the coordinator bridge companion. Respect installation
locks and existing session ownership. Then prepare a fresh private plugin package:

```powershell
.\repository\ai-plugin\Build-AIPlugin.ps1 -OutputDirectory .\.handoff\ai-plugin-package
```

The generated package pins the installed portable Python, installed bridge path,
and owned endpoint at `INTERLINKED-GUI-State/ai/bridge/endpoint.json` under the
existing MSI Project Library kit. It contains no credentials or model payloads.
Source folders are not deployed packages. Check `PLUGIN-CHECKSUMS.json`, register
the package through the existing personal marketplace workflow, and install/enable
**INTERLINKED AI** using the normal plugin manager. Preserve all other marketplace
entries. Packaging does not install, enable or prove Dot access.

The endpoint and its directory have a protected user-only DACL. The pipe rejects
remote clients and verifies the client SID; the client checks server PID, process
creation identity, SID and per-session identifier. Overlapped message-mode JSON
I/O is bounded by size and time. It does not deserialize Python objects. IPC
permissions are a same-user boundary, not isolation from malicious processes
already executing as that Windows user. Restricted sandbox tokens may be denied;
test under the actual connected-computer executor without weakening the DACL.

## Operations and acceptance

Read reduced cached state, search approved context, refresh observations, submit
bounded analysis/isolated workers, calibrate existing models, request load/unload,
retrieve/cancel bridge jobs, and explicitly update revision-checked AI settings.
The coordinator validates every operation independently of MCP schemas. Unload
requires local UI confirmation; no tool argument can assert that confirmation.
Roots, commands, pairing, application of results and save-back stay in the local
user/admin workflow. No automatic model downloads, runtime upgrades or tasks.

Run `python -B repository/app/test_ai_ipc.py` under normal Windows permissions for
real ACL, process-identity, cancellation/deadline and roundtrip fixtures. Run
`python -B repository/app/test_ai_plugin.py` for strict tool schemas and isolated
stdio behavior. Fixtures use private temporary state and no live model calls.
Verify an installed package read roundtrip separately, then ask actual Violet to
delegate a small supplied-text task on THIS-MACHINE and retrieve its result.
Record actual model provenance, not just a successful tool handshake. Test broad
management and an isolated worker diff before enabling routine use. MSI needs
its own reviewed pairing and live acceptance.

Protocol reference: [Windows named pipes](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew).
Plugin reference: [desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only).
