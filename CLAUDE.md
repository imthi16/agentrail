# AgentRail — Project Memory

AgentRail is a **Python-first, CLI-first local workflow control plane** that wraps
fast AI coding harnesses (**jcode**, Claude Code, Codex, Gemini CLI) as
**subprocesses** and layers governance, staging, and observability on top. It is a
**workflow supervisor, NOT an execution engine** — never reimplement harness
capabilities (editing, LLM calls) inside AgentRail.

See @README.md for the full vision/roadmap and @pyproject.toml for exact deps.

## Non-Negotiable Invariants (don't violate without updating README + an ADR)
1. **Enforced Capability Modes** — deterministic state machine enforced in Python
   at the subprocess-interception layer, independent of any LLM prompt. Modes:
   `plan` (read-only), `manual` (approval gate on every edit + shell command),
   `accept_edits` (edits auto-approved, shell needs approval), `auto` (full
   autonomy within an Intent Lock). Enforcement lives in code/hooks — **never** in
   this file. CLAUDE.md is context, not a security boundary.
2. **Stage-by-stage execution with git worktree isolation** — decompose a request
   into a DAG of stages; each stage gets its own worktree under
   `.agentrail/worktrees/<stage-id>` on branch `stage/<stage-id>`; each finished
   stage opens its own **draft PR** via `gh`; dependent stages are **stacked PRs**
   (child base = parent stage head branch). Canonical example: "implement login
   and logout as separate PRs" → two worktrees → two PRs.
3. **Model routing by effort tier (profiles)** — the model picker is TWO steps:
   family/provider first, then tier **Deep / Balanced / Fast**. Tiers map to real
   models + reasoning budgets (see agentrail/profiles/CLAUDE.md). Auto-downgrade
   mid-workflow (Deep to plan, Fast to lint) is allowed and logged.
4. **Tmux supervision** — long-running processes (compiles, tests, dev servers)
   run in persistent tmux sessions via `libtmux` so they survive control-plane
   crashes and stay attachable.

## Tech Stack & State Model
Python **3.11+**, Typer + Rich (CLI), **PyYAML** for state, `libtmux` (pre-1.0 —
pinned), GitPython or raw `git`, GitHub CLI `gh`, LSP diagnostics. FastAPI + React
dashboard are **v0.3 (remote)**, not the primary interface.

State is **file-based** (no SQLite): `.agentrail/config.yaml` (project config),
`.agentrail/workflow.yaml` (workflow + stage DAG + status),
`.agentrail/events/events.jsonl` (append-only timeline),
`.agentrail/checkpoints/<stage-id>/` (rollback snapshots),
`.agentrail/worktrees/<stage-id>/` (isolated worktrees). All runtime state under
`.agentrail/` is gitignored.

## Package Layout (flat `agentrail/` package)
Top-level modules: `cli.py` (commands), `config.py` (project config),
`models.py` (Workflow/Stage models), `workflow.py` (DAG planner + persistence).
Subsystems each have their own CLAUDE.md (loaded on demand):
`adapters/` `policies/` `workspaces/` `tmux/` `git/` `profiles/` `checkpoints/`
`events/`.

## Build / Test / Lint (run the full gate before every commit)
- Install (editable): `pip install -e ".[dev]"`  (or `uv pip install -e ".[dev]"`)
- Run CLI: `agentrail --help`
- Commands: `agentrail init | plan "<goal>" | status | run | pause | resume | rollback <stage-id>`
- Tests: `pytest -q`
- Types: `mypy agentrail`
- Lint/format: `ruff check agentrail tests && ruff format`
- **Full gate:** `ruff check agentrail && mypy agentrail && pytest -q`

## Execution Engine Facts (verified — do not "correct" these)
- Primary harness **jcode** (github.com/1jehuang/jcode, Rust, MIT). Run as a
  subprocess: `jcode run "<prompt>"`, resume `jcode --resume <name>`, or persistent
  `jcode serve` + `jcode connect`.
- jcode's RAM/latency figures (~27.8 MB PSS, ~14 ms first-frame) are the maker's
  **own README benchmarks** — cite as vendor-reported, not independent.
- jcode's README argues git worktrees are imperfect for multi-agent work;
  AgentRail deliberately imposes worktree isolation anyway (auditability + stacked
  PRs). This divergence is intentional — document it, don't silently undo it.

## Do / Don't
- DO gate every shell command and edit through the policies engine.
- DO create a checkpoint (git SHA + patch + manifest + tmux pane IDs) before any
  `auto` stage.
- DO keep each CLAUDE.md under ~200 lines; push detail into subsystem files.
- DON'T expose raw model strings in the picker — always route through profiles.
- DON'T run long processes as bare `subprocess.Popen` — use the tmux supervisor.
- DON'T trust the original ChatGPT spec's model names (e.g. "Claude 3.5 Opus" is
  not a real model); see agentrail/profiles/CLAUDE.md for verified IDs.
