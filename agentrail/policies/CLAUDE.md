# agentrail/policies/ — Capability Modes + Intent Lock

Deterministic enforcement of capability modes at the subprocess-interception
layer. This is a **security boundary implemented in Python** and must never
delegate enforcement to prompt text or CLAUDE.md. Model it on Claude Code's
PreToolUse-hook approach: intercept BEFORE the harness acts.

## Modes (state machine)
- `plan`: read-only. Allow file reads, grep, LSP diagnostics. Deny all writes and
  all shell commands.
- `manual`: read + write, but EVERY file edit and EVERY shell command raises an
  interactive approval gate before execution.
- `accept_edits`: file edits auto-approved; shell commands still require approval.
- `auto`: full autonomy strictly bounded by the active Intent Lock.

## Intent Lock (YAML file + hash)
Stored as a YAML file (e.g. `.agentrail/intent-lock.<stage-id>.yaml`) with fields:
`allowed_paths`, `denied_paths`, `shell_allow`, `shell_deny`. On load,
canonicalize the structure, compute a **SHA-256** hash, and record the hash in
`.agentrail/workflow.yaml` next to the stage. Every gated action re-verifies the
live lock against the recorded hash so a tampered lock is detected and rejected.
**Deny rules always win over allow rules.**

## Enforcement contract
- Pure decision function: `(mode, intent_lock, action) -> ALLOW | ASK | DENY`.
- Path checks operate on RESOLVED absolute paths (block `..`/symlink escapes out
  of `allowed_paths`).
- Shell checks match command prefixes against `shell_allow`/`shell_deny`.
- Log every decision to agentrail/events with trace_id/span_id and the rule that
  fired.

## Enforcement scope today (v0.2)
- **Wired:** `PolicyGate.authorize_session` — called once per stage from
  `runner.py` before the harness launches. Mode-level allow/ask/deny + Intent
  Lock hash verification; `auto` refused without a verified lock.
- **Implemented, NOT wired:** `run_shell` / `write_file` — the per-action path +
  shell scoping. No call site in `agentrail/`; they land with live harness
  session I/O. A running harness is unmediated inside its worktree.
  See `docs/adr/0001-admission-gate-vs-per-action-interception.md`.
- **Deliberately outside the gate:** the Semantic Edit Guard's post-harness
  checks (`checkpoints/checks.py`) run an allowlisted, code-defined command set.
  They are not configurable as free-form shell precisely because they bypass the
  gate and run with control-plane privileges.

## Do / Don't
- DO keep the engine deterministic and unit-testable with NO LLM in the loop.
- DO fail closed (DENY) on any ambiguity or hash mismatch.
- DON'T auto-approve shell commands in `accept_edits` — only edits are auto.
- DON'T rely on the harness's own permission model as a substitute for these
  modes; AgentRail enforces regardless of harness-native features.
- DON'T describe per-action interception as live in README/CLAUDE.md/docstrings
  until `run_shell`/`write_file` actually have a call site in `agentrail/`
  (a grep-checkable condition).
