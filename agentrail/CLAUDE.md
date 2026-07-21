# agentrail/ — Package Core (CLI, config, models, DAG planner)

Top-level modules for the control plane. Subsystems live in subpackages (each has
its own CLAUDE.md that loads on demand). Keep orchestration logic here thin —
delegate to subsystems.

## Modules
- `cli.py` — Typer app; entry point `agentrail` (`[project.scripts]` → `cli:main`).
  Commands: `version`, `init`, `plan`, `status`, `run`, `pause`, `resume`,
  `rollback`. Commands orchestrate; they don't contain subsystem logic.
- `config.py` — loads/writes `.agentrail/config.yaml` (project config: default
  mode, default profile, harness/adapter choice, budgets). Validate on load.
- `models.py` — typed dataclasses/pydantic models: `Workflow`, `Stage`,
  `IntentLock`, `Checkpoint`, `Profile`. These are the single source of truth for
  serialization to/from YAML.
- `workflow.py` — **DAG Planner** + persistence to `.agentrail/workflow.yaml`.

## DAG Planner contract (in workflow.py)
- Turn a natural-language goal into a JSON/YAML-schema-validated list of stages,
  each with `id`, `title`, `description`, `depends_on: [id...]`, acceptance
  criteria.
- Reject cycles; topologically sort before execution.
- Independent stages may run in parallel worktrees; dependent stages become
  stacked PRs (child base = parent head branch — see agentrail/git/CLAUDE.md).
- Planning runs at the **Deep** profile tier by default (agentrail/profiles).
- Persist the plan to `.agentrail/workflow.yaml` in `awaiting_approval` status;
  execution only begins after explicit approval.

## Canonical example (matches README)
Goal "Implement login and logout as separate PRs" →
`analyse` (depends_on: []) → `login` (depends_on: [analyse]) and
`logout` (depends_on: [analyse]) → two isolated worktrees → two draft PRs.

## Conventions
- All state mutations go through `models.py` serialization; never hand-edit YAML in
  code paths.
- Every command emits events (agentrail/events) with trace/span IDs.
- Keep functions pure where possible; side effects (git, tmux, fs) live in
  subsystems and are injected, so `workflow.py`/`models.py` stay unit-testable.

## Do / Don't
- DO validate the workflow YAML against the schema on read and write.
- DON'T let a stage depend on the uncommitted working-tree state of another
  stage's worktree — only on its committed head.
