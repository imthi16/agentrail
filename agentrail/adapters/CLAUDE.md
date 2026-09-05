# agentrail/adapters/ — Harness Adapters (jcode / Claude Code / Codex / Gemini)

Uniform adapter interface so the control plane treats every harness as an
interchangeable subprocess. **jcode is the primary/default adapter.** Every harness
runs inside the tmux supervisor and under the policies engine, regardless of the
harness's own permission features.

## Interface (each adapter implements)
- `start(prompt, mode, model_id, cwd) -> handle`
- `send(handle, text)` — feed input to a running session
- `capture(handle) -> str` — read current output
- `stop(handle)` — terminate cleanly
- `capabilities() -> {...}` — declare: session resume? swarm? which providers?
  supports non-interactive run? Plus three fields the run loop branches on:
  - `process_backed` — an OS process, so it is supervised in a tmux pane.
    False for API adapters (OpenCode): an HTTP call gains nothing from a pane.
  - `model_selection` — the adapter can actually APPLY a routed `model_id`.
    False means routing is still resolved and logged, then reported as
    `profile.model_not_applied` rather than silently dropped.
  - `edits_files` — mutates its worktree. False makes an empty worktree an
    expected outcome (`stage.no_changes` with `advisory_only`), not a failure.

## jcode specifics (verified — github.com/1jehuang/jcode, Rust, MIT)
- Non-interactive: `jcode run "<prompt>"`.
- Resume by memorable name: `jcode --resume <name>`.
- Persistent server: `jcode serve` + `jcode connect`.
- Providers: `jcode login --provider <claude|openai|gemini|...>`.
- Has native swarm coordination + semantic-vector memory. AgentRail STILL runs each
  stage in an isolated worktree and enforces modes at the interception layer — do
  not rely on jcode's swarm as a substitute for AgentRail staging.
- jcode's published RAM/latency numbers are vendor-reported; don't cite as
  independent measurements.

## Other adapters
- **Claude Code** — has its own permission modes (plan/acceptEdits/auto/…) and an
  Effort selector; AgentRail maps its modes/profiles onto Claude Code flags but
  keeps its own enforcement.
- **Codex** (OpenAI CLI) — map profiles' `reasoning_effort` values here.
- **Gemini CLI** — defaults to a read-only Plan Mode; map AgentRail `plan` onto it
  and use `thinking_level` from profiles.

## Model flags: never guess
There is **no verified jcode model flag**. Hardcoding one would break every real
invocation with an unknown-argument error — making the routing claim more false,
not less. `JcodeAdapter.model_flag` is opt-in and operator-asserted, supplied via
`config.harness_options.model_flag`; the default argv is unchanged. Ship a flag
by default only after verifying it against a pinned harness version.

## Do / Don't
- DO normalize every harness's diverse config into AgentRail's single policy +
  profile model.
- DON'T ship a harness CLI flag AgentRail has not verified.
- DON'T assume a harness's native permissions replace AgentRail's capability modes.
