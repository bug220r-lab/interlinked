# Codex workstation handoff

THIS-MACHINE is the primary Codex and MAGIX workstation. MSI provides project storage and explicitly requested background jobs. The source checkout, installed client and working data have separate folders.

## Open the project on THIS-MACHINE

In Codex on THIS-MACHINE, add a local project using:

```text
C:\Users\Jude\Documents\Codex\INTERLINKED
```

Name it **INTERLINKED** and make this its primary folder. Select the outer checkout containing `AGENTS.md`, `.git` and `repository`, not the nested `repository` folder. Codex uses the primary folder for automatic project guidance and Git context. Preparing a folder through SMB does not register a project in the Codex app on the other PC.

Start the new chat with:

> Read AGENTS.md and .handoff/START-HERE.md, then the referenced HANDOFF.md. This is THIS-MACHINE, the primary INTERLINKED development workstation; MSI remains the storage and compute host. Verify the checkout, installed kit, and library connection read-only first. Preserve active project caches and sessions. Summarize the accepted workflows and outstanding media/font items before making changes.

The private `.handoff` package contains selected evidence and a continuation summary, not the entire old chat. Current installed state remains authoritative.

## Which data to pull

| Need | Location and method |
| --- | --- |
| Source and reviewed tests | This Git checkout, with origin at the existing private GitHub repository. Authenticate on THIS-MACHINE when needed. Never copy MSI authentication stores. |
| Setup context and receipts | Pinned capsule under `\\MSI\ProjectLibrary\Start-Here`; use `Pull-INTERLINKED-Context.ps1` to create a new verified `.handoff` snapshot. |
| Installed launcher/runtime | `C:\Users\Jude\Documents\MSI-Project-Library`; already installed. Inspect in place; do not overlay it with this source distribution. |
| Selected MAGIX media | Open the installed `INTERLINKED.cmd`, select a family and **Prepare & open MAGIX**. |
| Other published coding/data projects | Open installed `Project-Library-Client\Project-Library.cmd`; use its real catalog IDs and fetch actions. |
| Saved changes | Save/close the application, finish the workspace session, then **Save to MSI + verify**. Use **Free local space** only explicitly after verification. |
| MSI compute jobs | Read the installed `SSH-JOBS-START-HERE.txt` with the newer handoff status. Do not replay consumed one-shot verification plans as new jobs. |

The older catalog contains no INTERLINKED entry. Its source comes from Git and private context from the separate capsule. Full media stays on MSI until a selected project is fetched. Existing local edits and deletions must be preserved. Never use loose folder synchronization over managed caches.

## Pull a context snapshot

Use the exact capsule name and SHA-256 supplied in `.handoff/START-HERE.md`, running normally on THIS-MACHINE:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\repository\handoff\Pull-INTERLINKED-Context.ps1 -CapsuleName "<provided capsule name>" -ManifestSha256 "<provided manifest SHA-256>"
```

The execution-policy argument applies only to this invocation. The helper reads only the pinned canonical Start-Here capsule, checks every file, and writes a fresh `.handoff/<capsule>-pull-<UTC>-<id>` directory plus a receipt. It does not execute capsule contents or overwrite installed state. Interrupted results remain incomplete; a retry creates another snapshot. Keep `.handoff` outside Git.

The capsule is bounded to 200 metadata/evidence files, 32 MiB per file and 64 MiB total. Paths, extensions and hashes are validated; links and cloud placeholders are refused. A newly published capsule needs its own explicit manifest hash. This helper does not fetch media or discover arbitrary later files on MSI.

## Check the handoff

Read `.handoff/PREPARATION-RECEIPT.json` for the exact commit, manifest hash and copied-file checks. The bundle-based checkout does not transfer GitHub credentials; new-PC authentication remains a separate check.

Run source integrity with Python 3.12, or the existing installed portable runtime:

```powershell
& "$env:USERPROFILE\Documents\MSI-Project-Library\Project-Library-Client\Runtime\python.exe" -I -B .\repository\verify_source_snapshot.py
```

The pull helper passed 28 native synthetic checks. To run its fixtures separately from production:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\repository\handoff\Test-Context-Pull.ps1
```

Use the installed `Check-MSI-Connection.cmd` for live library access. Source integrity, synthetic tests, installed-byte verification and actual new-PC use are separate acceptance checks. Historical machine-specific scripts or tests may require excluded private inputs; never weaken their pins to run them elsewhere.

Official project-folder guidance: https://learn.chatgpt.com/docs/projects?surface=app
