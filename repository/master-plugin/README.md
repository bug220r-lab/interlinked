# INTERLINKED MASTER local bridge

This desktop-only package exposes four local stdio MCP tools: `get_master_status`, `publish_violet_status`, `get_master_attention`, and `acknowledge_master_attention`. It uses the installed reviewed portable Python runtime and installed MASTER bridge, not the source checkout as a deployed kit. The core server uses Python's standard library; no package installation, API key, public port, tunnel or model call is required.

## Build and connect

After the reviewed application package is installed, prepare a fresh private package:

```powershell
.\repository\master-plugin\Build-MasterPlugin.ps1 -OutputDirectory .\.handoff\master-plugin-package
```

Keep generated private packages outside Git. The checked-in compatibility manifest references the generated `.mcp.json`; the source folder is not itself a runnable installed package. For the supported personal-marketplace workflow, place the reviewed package in `~/.codex/plugins/interlinked-master` and add its entry to `~/.agents/plugins/marketplace.json`, preserving any existing entries:

```json
{
  "name": "interlinked-local",
  "plugins": [{
    "name": "interlinked-master",
    "source": {"source": "local", "path": "./.codex/plugins/interlinked-master"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity"
  }]
}
```

Restart the ChatGPT desktop app, find **INTERLINKED MASTER** in the local source in the Plugins Directory, and install/enable it. Test in a new applicable local chat. Preparing a package or marketplace proposal does not install a plugin. Local tools require the installed reviewed bridge, so finish the guarded INTERLINKED deployment before installation.

The bridge reads only a reduced snapshot under `%LOCALAPPDATA%\INTERLINKED\MASTER-status`. It writes only the short `violet.json` activity record and bounded attention acknowledgments there. It never exposes the INTERLINKED HTTP capability token or methods for shell commands, editing, jobs, sessions, save-back, release, or changing the LCD. Reported activity expires after two minutes without another actual update; expiry means unknown rather than idle or completed.

The private package pins the state root to `C:\Users\Jude\AppData\Local\INTERLINKED\MASTER-status` through `--state-root`. Some restricted local executors redirect `LOCALAPPDATA`; the explicit reviewed path keeps the bridge on the installed feed. Existing directory ownership checks still apply. Registering the source in a marketplace makes it available for normal installation; it does not install, enable or verify it for Violet.

For actual Dot acceptance, ask Violet to read MASTER status through the installed plugin and publish a short update. Check that the display shows the new timestamp and Dot-reported source. Local fixture MCP tests do not prove account installation or Dot/cloud tool availability. If the plugin is unavailable to a cloud-orchestrated Dot, use a supported connected-computer/local-task context. Do not silently expose an unauthenticated public server to make cloud access work.

## Passive Codex observation

The installed `master_hook.py --show-config` emits the metadata observer definitions. Use `--existing-config <existing hooks.json> --proposal <new private file>` to prepare an additive merged proposal. The command never modifies the active hook configuration or trust records. Preserve unrelated hooks and inline config, review the exact resulting observer command, then place the reviewed merged configuration at `~/.codex/hooks.json`. In the Codex CLI, open `/hooks` to review and trust the exact new definitions. New/changed non-managed hooks remain skipped until trusted. No trust record, approval setting or bypass flag is modified by this package.

The observer filters to MASTER's registered activity roots before spooling events. It discards prompt text, transcript paths, tool arguments and results. It emits no model context or policy decisions, uses one-second timeouts, and fails silently so telemetry cannot block coding. Unsupported cloud events remain unavailable; a quiet source never proves task completion.

## Project build/test evidence

An explicit producer can write an atomic result record to `MASTER-status/results/<project-id>.json`:

```json
{
  "format": "interlinked-master-result-v1",
  "projectId": "interlinked",
  "kind": "test",
  "result": "passed",
  "observedAt": "2026-10-30T00:00:00Z",
  "baseline": {"head": "<exact tested commit SHA>", "clean": true},
  "summary": "Reviewed test suite passed"
}
```

The current implementation accepts success/failure as current only when the Git HEAD matches and the working copy is clean. Dirty or changed work retains the previous observation but displays health as unknown. No command is executed and no test result is fabricated by MASTER. Private receipts never belong in Git.

## Validation

```powershell
python -B repository/app/test_master.py --output .handoff/master-tests-new --worker .handoff/master-build/native-v3/INTERLINKED.Master.Worker.exe
```

The synthetic suite checks privacy filtering, unrelated-chat exclusion, out-of-order and duplicate lifecycle events, freshness, result baselines, ownership refusal, MCP roundtrips under isolated Python, and native preview framing. It does not connect to the USB device, MSI, or the actual Dot account.

After the installed MASTER service is running, `python -B repository/master-plugin/test_master_plugin.py --output <fresh-private-folder>` validates package structure, fixed installed-runtime/bridge/state paths, unsafe configuration refusals and a read-only live MCP request. It never publishes an update as Violet, changes plugin activation or alters hook trust. A passing read request proves the local bridge works, not that the actual Dot can call it.

Official references: [plugin packaging](https://developers.openai.com/plugins/build/plugins), [desktop-only MCP plugins](https://developers.openai.com/plugins/deploy/desktop-only), and [Codex hooks](https://learn.chatgpt.com/docs/hooks).

## Authorized lifecycle reporting (v1.1.1)

The skill treats the user's standing reporting instruction as authorization for Violet's meaningful start, input-wait, interruption and turn-finish updates. Violet must carry that responsibility into each delegated task. Read-only project inspection may still send the separately authorized, bounded MASTER metadata record; an explicit instruction not to publish MASTER status always wins. A read request itself remains read-only and never impersonates Violet. Ordinary Codex lifecycle observations are not converted into Violet reports.

This is an instruction-only plugin update. It adds no tools, permissions, native hooks, timers or model polling, and requires no application replacement. Update the registered local package, then use `codex plugin add interlinked-master@interlinked-local --json` to install the reviewed version through the normal plugin manager. Verify the installed version and skill bytes; do not edit the plugin cache or hook trust records manually. Existing chats may retain earlier context, so include the corrected reporting contract when continuing work with Violet or delegating a new task.

For actual automatic-report acceptance, ask Violet to delegate a harmless read-only project check **with MASTER metadata writes expressly allowed**. Independently observe fresh `local_task` working and turn_finished reports, with no project mutation or alert acknowledgment. Keep a task with explicit no-publish instructions as a negative control. A successful bridge smoke test or general project hook does not establish this actual Violet behavior. New delegated tasks receive task-specific context rather than every Dot conversation; see [Tasks and memory](https://learn.chatgpt.com/docs/dots/tasks-and-memory).

## Sourced reports and attention (v1.1)

Set `reporter` to `dot` for a report made by the actual Dot, `local_task` for a delegated local task, or `demo` for synthetic work. Omitted reporters and legacy v1 records are unverified and cannot establish a connected feed. These labels are self-reported provenance, not identity authentication or an account-connection test. A fresh local-task report establishes only a delegated route. New reports use `interlinked-violet-status-v2`; the public snapshot remains v1 with additive reporter/connection-route and attention fields.

MASTER keeps up to 100 timestamped build/test failures and requests for input in its owned status folder. Queue reads never acknowledge items. Explicit acknowledgment marks only the retained alert as reviewed; it does not repair or resolve a project failure. Repeated observations deduplicate by project, kind and observation time. Old items and acknowledgments are evicted together. Corrupt queue data is unavailable and is preserved for review rather than reported healthy. Queue summaries are fixed metadata; no prompts, commands or raw results are added.

The desktop and cooler show pending counts and sourced activity. Failures take expression priority over input requests, then working activity; all-unknown sources use a neutral expression. Delivery is queued for Violet's next requested check, with no model polling, Platform key, tunnel, or automatic Dot wake-up. A real read/publish roundtrip is still required; fixture reports never prove actual Violet access.
