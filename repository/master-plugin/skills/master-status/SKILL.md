---
name: master-status
description: Read INTERLINKED MASTER project activity and health, or publish Violet's own short update to its local display. Use for explicit MASTER display/status work. Never infer project success from a chat ending.
---

Use the connected INTERLINKED MASTER tools on the user's connected computer.

1. Call `get_master_status` to inspect the current metadata. Respect `available`, observation times, and stale/unavailable source labels.
2. When asked to report work to MASTER, call `publish_violet_status` with one of `working`, `waiting`, `turn_finished`, `interrupted`, or `unknown`. Set `reporter` to `dot` only for a report made by the actual Dot, `local_task` for a delegated local task, or `demo` for synthetic demonstrations. Omission means unverified. Include a short description of actual work and only a registered project ID when relevant. Reporter labels describe provenance; they are not identity authentication.
3. Keep the update within 120 characters. Never include credentials, prompts, file contents, command arguments, personal paths, or private message text.
4. Respect the returned source and expiry. A local task does not prove direct Dot tool access. Do not claim reports establish measured project health, continuous online presence, or a live-animation feed. A turn ending does not mean builds/tests passed or the project is finished.
5. These tools cannot start, stop, save, release, or otherwise control projects. Do not try to invoke project actions through this bridge.
6. At the next requested status check, call `get_master_attention` to review queued failures and input requests. A read does not acknowledge them. Call `acknowledge_master_attention` with reviewed IDs only when explicitly asked to acknowledge those items. Acknowledgment never resolves the underlying failure. These are historical observations with timestamps, not guaranteed current failures. No automatic wake-up or continuous polling is configured.

If tools are unavailable in the Dot's cloud context, report that limitation. A local desktop-only plugin is not proof of cloud access. Use the user's supported connected-computer/local-task workflow, and verify a real read/publish roundtrip before claiming the actual Dot is connected. Do not expose the local service publicly or bypass plugin/trust controls.
