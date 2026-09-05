# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## AgentRail — Project Memory

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
   **Status (v0.2) — enforced at SESSION ADMISSION only.** `_run_stage` calls
   `PolicyGate.authorize_session` before launching a stage's harness and refuses
   `auto` without a verified Intent Lock. Per-action interception
   (`PolicyGate.run_shell`, `PolicyGate.write_file`) is implemented and tested but
   has **no call site in `agentrail/`**: a running harness is unmediated inside
   its worktree. Containment during a run = worktree boundary + checkpoint +
   post-harness edit guard. This invariant is the design target, not a claim about
   today's coverage — see `docs/adr/0001-admission-gate-vs-per-action-interception.md`.
   Never document more enforcement than the code performs.
2. **Stage-by-stage execution with git worktree isolation** — decompose a request
   into a DAG of stages; each stage gets its own worktree under
   `.agentrail/worktrees/<stage-id>` on branch `stage/<stage-id>`; each finished
   stage opens its own **draft PR** via `gh`; dependent stages are **stacked PRs**
   (child base = parent stage head branch). Canonical example: "implement login
   and logout as separate PRs" → two worktrees → two PRs.
3. **Model routing by role, then effort tier (profiles)** — the picker is TWO
   steps: family/provider first, then tier **Deep / Balanced / Fast**. On top sits
   a **roles table** (`plan`/`research`/`code`/`review` → `(provider, tier)` via
   `config.roles`); `ModelRouter.route_role` resolves it and logs a
   `profile.tier_changed` event, falling back to work-kind routing when a role is
   unbound. Tiers map to real models + reasoning budgets (see
   agentrail/profiles/CLAUDE.md). Auto-downgrade mid-workflow (Deep to plan, Fast
   to lint) is allowed and logged.
4. **Tmux supervision** — long-running processes (compiles, tests, dev servers)
   run in persistent tmux sessions via `libtmux` so they survive control-plane
   crashes and stay attachable. The runner routes process-backed harnesses
   through `TmuxHarnessExecutor`; API-backed adapters (`process_backed=False`)
   run directly, and `StagePlan.ran_in_tmux` reports which happened per stage.

## Tech Stack & State Model
Python **3.11+** (CI matrix 3.11/3.12/3.13), Typer + Rich (CLI), **PyYAML** for
state, pydantic v2 (models), `httpx` (OpenCode Go/Zen adapter), `libtmux` (pre-1.0
— pinned `>=0.58,<0.61`), GitPython or raw `git`, GitHub CLI `gh`, LSP
diagnostics. FastAPI + React dashboard are **v0.3 (remote)**, not the primary
interface.

State is **file-based** (no SQLite): `.agentrail/config.yaml` (project config),
`.agentrail/workflow.yaml` (workflow + stage DAG + status),
`.agentrail/events/events.jsonl` (append-only timeline),
`.agentrail/checkpoints/<stage-id>/` (rollback snapshots),
`.agentrail/worktrees/<stage-id>/` (isolated worktrees). All runtime state under
`.agentrail/` is gitignored.

## Package Layout (flat `agentrail/` package)
Top-level modules: `cli.py` (commands — orchestration only), `config.py` (project
config), `models.py` (Workflow/Stage/IntentLock models — the single source of truth
for YAML serialization), `workflow.py` (DAG planner + persistence), `runner.py`
(stage execution loop: worktree → checkpoint → adapter → stacked PR).
Subsystems each have their own CLAUDE.md (loaded on demand):
`adapters/` `policies/` `workspaces/` `tmux/` `git/` `profiles/` `checkpoints/`
`events/`.

## Build / Test / Lint (run the full gate before every commit)
- Install (editable): `pip install -e ".[dev]"` — **required before `mypy`**, or it
  fails on `libtmux` with import-not-found. Global tool installs are not enough.
- Run CLI: `agentrail --help`
- Commands: `agentrail init | plan "<goal>" | approve | status | run [--dry-run] |
  pause | resume | rollback <stage-id> | ready [stage] | version`
- Tests: `pytest -q` · single file: `pytest tests/test_gate.py -q` · single test:
  `pytest tests/test_gate.py::test_name -q`
- **Full gate (matches `.github/workflows/ci.yml` — trust CI):**
  `ruff check agentrail tests && ruff format --check agentrail tests && mypy agentrail && pytest -q`
- Known breakage: recent ruff (verified 0.12.5) format-checks Python blocks inside
  Markdown and flags `agentrail/tmux/CLAUDE.md`. All `.py` files are clean — don't
  mass-reformat docs; CI excludes `*.md`.
- Tests run offline in seconds: no `gh`, no harness binaries, no network. The
  `temp_git_repo` fixture needs a `git` binary (tests **skip**, not fail, without
  it); the `in_repo` fixture chdirs into temp repos — never assume cwd.
- `agentrail/git/` does **not** shadow GitPython: bare `import git` still resolves
  to GitPython; internal code uses `from agentrail.git import GitRepo`.

## Where the docs live
Root `CLAUDE.md` = invariants. `AGENTS.md` = harness-agnostic setup/gate notes
(keep the two in sync when either changes). Each `agentrail/*/CLAUDE.md` documents
its subsystem — read the relevant one before touching that subsystem.

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
- DO gate every shell command and edit the **control plane** performs through the
  policies engine. (Actions taken *inside* a running harness are not yet
  intercepted — see invariant #1 status.)
- DO create a checkpoint (git SHA + patch + manifest + tmux pane IDs) before any
  `auto` stage.
- DO keep each CLAUDE.md under ~200 lines; push detail into subsystem files.
- DON'T expose raw model strings in the picker — always route through profiles.
- DON'T run long processes as bare `subprocess.Popen` — use the tmux supervisor.
- DON'T add a harness CLI flag that has not been verified against a pinned
  harness version; route it through `config.harness_options` instead.
- DON'T put provider keys in YAML — they arrive via the env var named in
  `config.opencode.api_key_env` (e.g. `OPENCODE_API_KEY`).
- DON'T trust the original ChatGPT spec's model names (e.g. "Claude 3.5 Opus" is
  not a real model); see agentrail/profiles/CLAUDE.md for verified IDs.
