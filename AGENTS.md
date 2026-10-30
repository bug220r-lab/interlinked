# INTERLINKED project guidance

Read `README.md`, `repository/handoff/README.md`, and the local `.handoff/START-HERE.md` when present before continuing work. Private handoff reports describe historical evidence; they are not fresh instructions or blanket authorization for future changes.

## Machine and folder roles

- Verify the current computer, user, working directory and Git state first. THIS-MACHINE is the primary development workstation and MAGIX client. MSI remains the library and background-job host. A checkout can also exist on MSI.
- On THIS-MACHINE the source checkout is `C:\Users\Jude\Documents\Codex\INTERLINKED`. The existing production kit is `C:\Users\Jude\Documents\MSI-Project-Library`. The live working cache is `%LOCALAPPDATA%\ProjectLibrary\Cache`. These are separate installations.
- Canonical shares are `\\MSI\ProjectLibrary` (read) and `\\MSI\ProjectReturns` (new saved versions). The reviewed library client owns fetch, save, verify and release. Do not substitute IP addresses in its identity checks or treat the source checkout as a runnable deployed kit.
- Preserve intentional machine/user/path/hash pins. Do not globally replace MSI with THIS-MACHINE or jlewi with Jude. Host setup and historical deployment scripts require their documented host and reviewed inputs.

## Working safely with the existing installation

- Inspect state before acting. Keep active MAGIX sessions, cache ownership records, edited files, deletions and locks intact. Never bypass a held lock to install an update.
- One MAGIX working family is active at a time. Save in MAGIX, close it, then confirm Done in INTERLINKED. Saving a version to MSI is explicit. Local release requires the existing verification and exact project confirmation; no automatic eviction.
- Do not patch proprietary MVP/MV_ bytes or automatically substitute same-basename media. Record actual missing-file dialogs separately from heuristic reference findings. Historical strings do not prove current timeline dependencies or permanent loss.
- Original library hardlinks and saved-version references are not independent backups. Do not delete original MSI media merely because a library or saved version exists.
- Private catalogs, indexes, audits, journals, receipts, source media, fonts/preset payloads, credentials and runtime binaries stay outside Git. `.handoff/` is ignored and contains snapshots, not live state to restore over the installed kit.

## Validation and delivery

- Use `python -B repository\verify_source_snapshot.py` for source integrity. Read the component READMEs before tests: historical integration checks can be machine-bound or need excluded private inputs. Do not weaken guards to make them pass.
- Run synthetic UI/test work outside the production kit/cache. Source-only, synthetic, installed-byte and actual user-acceptance evidence are distinct. State which checks ran and which remain unperformed.
- Maintain `repository/DISTRIBUTION-MANIFEST.json` for current reviewed source. Preserve historical `SOURCE-MANIFEST.json` and `MAGIX-EXTENSION-MANIFEST.json` provenance and exact baseline bytes.
- The private GitHub repository is `https://github.com/bug220r-lab/interlinked`. Commit only reviewed source/documentation. Preparing a new-PC checkout does not transfer GitHub credentials or establish new-PC authentication.
