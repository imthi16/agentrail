# AGENTS.md

AgentRail is a **workflow supervisor, not an execution engine**: it wraps coding
harnesses (jcode, Claude Code, Codex, Gemini CLI) as subprocesses and adds
governance, worktree staging, and observability. Never reimplement harness
capabilities (editing, LLM calls) inside AgentRail.

`CLAUDE.md` (root) holds the full invariants; each `agentrail/*/CLAUDE.md`
documents its subsystem. Read the relevant one before touching a subsystem.

## Setup and verification gate

```bash
pip install -e ".[dev]"        # REQUIRED before mypy — else libtmux import-not-found
ruff check agentrail tests
ruff format --check agentrail tests
mypy agentrail                 # strict + pydantic.mypy plugin
pytest -q
```

- Without the editable install, `mypy agentrail` fails on `libtmux`
  (import-not-found). Global tool installs alone are not enough.
- CI (`.github/workflows/ci.yml`) runs exactly this gate on Python 3.11, 3.12,
  3.13. Note CI lints **and format-checks `agentrail` and `tests`**, while the
  root `CLAUDE.md` gate omits the format check and lints only `agentrail` —
  trust CI.
- Known breakage: with recent ruff (verified 0.12.5), `ruff format --check`
  formats Python code blocks inside Markdown and currently flags
  `agentrail/tmux/CLAUDE.md`. All `.py` files are clean; don't mass-reformat
  Markdown docs to "fix" this — it's a docs-code-block style issue.
  CI excludes `*.md` from `ruff format --check` to avoid this.
- Single test file: `pytest tests/test_gate.py -q` (standard pytest, no extras).

## Tests: what they need

- ~137 tests run in seconds, offline. No `gh`, no harness binaries, no network.
- The `temp_git_repo` fixture (tests/conftest.py) requires a `git` binary —
  tests **skip** (not fail) without it.
- E2E tests chdir into temp repos via the `in_repo` fixture; don't assume cwd.

## Toolchain quirks

- Ruff: `line-length = 100` (not 88), select `E,F,I,UP,B`, target `py311`.
- mypy is **strict** with the `pydantic.mypy` plugin; models in `models.py`
  are pydantic v2.
- `libtmux` is pinned `>=0.58,<0.61` on purpose (pre-1.0 API churn). Don't bump
  casually — re-verify the `Server → Session → Window → Pane` API on upgrade.
- This repo has its own `agentrail/git/` package. It does **not** shadow
  GitPython: bare `import git` still resolves to GitPython; subsystem code is
  `from agentrail.git import GitRepo`.

## Architecture facts that change how you code

- Entry point: `agentrail = agentrail.cli:main` (Typer). `cli.py` commands
  orchestrate only — subsystem logic lives in the subpackages.
- `models.py` is the single source of truth for YAML serialization. Never
  hand-write YAML state in code paths.
- All runtime state is file-based under `.agentrail/` (config.yaml,
  workflow.yaml, events.jsonl, checkpoints/, worktrees/) and gitignored.
- Capability modes (`plan`/`manual`/`accept_edits`/`auto`) and Intent Lock are
  enforced **in Python**, never via prompts or instruction files. Deny rules win;
  fail closed. **Scope today: session admission only** — the gate runs once
  before a stage's harness launches; `PolicyGate.run_shell`/`write_file` (the
  per-action layer) have no call site in `agentrail/` yet, so a running harness
  is unmediated inside its worktree. See root CLAUDE.md invariant #1 and
  `docs/adr/0001-admission-gate-vs-per-action-interception.md`.
- Long-running processes go through the tmux supervisor (`agentrail/tmux/`),
  never bare `subprocess.Popen`. The runner supervises adapters declaring
  `process_backed`; API adapters run directly. `ran_in_tmux` reports which.
- Model IDs are never hardcoded — always route through the `profiles/` config
  table (`provider, tier`); note `claude-haiku-4-5-20251001`'s date suffix is
  part of the ID. A routed model only reaches a harness that declares
  `model_selection`; otherwise it is logged as `profile.model_not_applied`.
  Never add an unverified harness CLI flag — use `config.harness_options`.
- One stage = one worktree `.agentrail/worktrees/<stage-id>/` on branch
  `stage/<stage-id>`; dependent stages produce **stacked** draft PRs whose base
  is the parent stage's head branch (never `main`). PRs go through `gh`, push
  before create, degrade to dry-run when `gh`/remote is missing.
- Rollback is always scoped to a single stage's worktree, never the main
  checkout.
