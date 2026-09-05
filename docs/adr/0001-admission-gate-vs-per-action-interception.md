# ADR 0001 — Enforcement scope: session admission now, per-action interception later

- **Status:** Accepted
- **Date:** 2026-09-05
- **Affects:** Invariant #1 (Enforced Capability Modes) in root `CLAUDE.md`

## Context

Root `CLAUDE.md` requires that capability modes be "a deterministic state machine
enforced in Python at the subprocess-interception layer, independent of any LLM
prompt", and `README.md` presents the mode table as shipped behaviour.

The code enforces less than that wording implies. `WorkflowRunner._run_stage`
calls `PolicyGate.authorize_session(argv)` exactly once per stage: a single
allow / ask / deny on whether the harness may launch, including Intent Lock hash
verification and the refusal to run `auto` without a verified lock. Once that
returns approved, the harness subprocess runs to completion with no further
mediation. `PolicyGate.run_shell` and `PolicyGate.write_file` — which implement
the real per-action path and shell scoping, and are covered by
`tests/test_gate.py` and `tests/test_policies.py` — have **no call site anywhere
in `agentrail/`**.

Closing that gap properly requires observing each tool call a harness makes and
gating it before it takes effect. That needs a harness-side hook or permission
callback surface. jcode's verified interface (`run`, `--resume`, `serve` /
`connect`, `login --provider`) exposes no such surface that we have confirmed,
and AgentRail's rule is not to build against unverified harness behaviour.

## Decision

Ship session-admission enforcement as the enforcement boundary for now, and
state that scope plainly in the docs rather than implying per-action coverage.

Specifically:

1. `authorize_session` remains the enforcement point. Mode semantics
   (`plan` denies, `manual` asks, `accept_edits`/`auto` allow, `auto` requires a
   verified lock) are unchanged and still enforced in Python.
2. `run_shell` / `write_file` stay implemented and tested, ready for the call
   site that arrives with live harness session I/O.
3. Every document that describes mode enforcement says which half is live.
4. We do **not** ship a hook that merely logs harness actions. Something that
   looks like enforcement without enforcing is worse than an honest gap.

## Consequences

While a stage's harness runs, containment comes from:

- **Worktree isolation** — the harness is confined to
  `.agentrail/worktrees/<stage-id>/`, never the main checkout.
- **Pre-stage checkpoints** — a snapshot exists before any `auto` stage.
- **The Semantic Edit Guard** — post-harness blast-radius checks that revert the
  worktree to its checkpoint on failure.
- **The append-only event timeline** — what ran is auditable after the fact.

What is *not* contained: a harness may read, write, and execute anything its own
permissions allow inside its worktree, regardless of the stage's Intent Lock
`allowed_paths` / `shell_allow`. The Intent Lock currently binds admission, not
behaviour. Treat it as scoping intent, not as a sandbox.

This is a real narrowing of invariant #1's wording, which is why it is recorded
here rather than quietly absorbed.

## Revisit when

Any of these makes per-action enforcement implementable, at which point this ADR
should be superseded:

- A harness exposes a verified hook / permission-callback API that AgentRail can
  drive (a pre-tool-use callback that can deny).
- AgentRail gains live session I/O and can mediate a harness's tool calls in the
  loop rather than launching and waiting.
- Execution moves into a sandbox (container, seccomp/landlock) where path and
  command scoping can be enforced by the OS instead of by cooperation.

Until then, the grep-checkable rule in `agentrail/policies/CLAUDE.md` applies:
do not describe per-action interception as live until `run_shell` / `write_file`
have a call site in `agentrail/`.
