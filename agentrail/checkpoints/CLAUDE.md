# agentrail/checkpoints/ — Snapshots, Retries, Rollback

File-based checkpoints (no database). A checkpoint captures enough state to
restore a stage's worktree to a known-good point ("local time travel").

## Checkpoint contents (per stage)
Written under `.agentrail/checkpoints/<stage-id>/<checkpoint-id>/`:
- `git_head.txt` — the worktree's HEAD SHA.
- `changes.patch` — serialized diff of tracked + untracked changes.
- `manifest.json` — active file-tree manifest (paths + hashes).
- `tmux.json` — recorded session/window/pane IDs for the stage.
Index checkpoints for a stage in `.agentrail/workflow.yaml` (ordered list).

## Rollback (`agentrail rollback <stage-id>`)
1. Suspend the stage's tmux pane(s) recorded in the checkpoint.
2. Within THAT worktree only: `git reset --hard <git_head_sha>`.
3. Delete untracked artifacts created after the checkpoint.
4. Restore/kill tmux panes as recorded, then re-emit a rollback event.
Rollback is ALWAYS scoped to a single worktree — never the main checkout.

## Semantic Edit Guard (enforced with policies)
- **Read-before-edit:** the edit must be based on the current file content (reject
  blind overwrites of unread files/symbols).
- **Post-edit:** run the configured checks from `checks.py` inside the stage
  worktree, scoped to files changed since the checkpoint (so pre-existing
  breakage in untouched files never blocks a stage).
- **Blast-radius check:** if an edit introduces a syntax error, a type collision
  outside `allowed_paths`, or fails tests → auto-revert from the last checkpoint
  patch and retry the edit with the error log attached to the prompt context.

## Shipped checks (`checks.py`, config key `guard:`)
An ALLOWLIST (`GuardCheck`), never free-form shell: the guard runs outside
`PolicyGate` with control-plane privileges, and `.agentrail/config.yaml` is
writable from inside a worktree — arbitrary commands there would let a harness
escape its Intent Lock through the subsystem meant to catch bad edits.
- Default: `python_syntax` only (stdlib `ast.parse`; works with no toolchain).
- Opt-in: `ruff`, `ruff_format`, `mypy`, `pytest`. **`pytest` imports the
  worktree's `conftest.py` into the control-plane process** — enable knowingly.
- Enabling ruff/mypy/pytest by default would need per-check baseline comparison
  first, or stages get reverted for findings that predate the run.
- Checks fail OPEN when they cannot check (tool missing, timeout) and closed on a
  real failure: this is a QUALITY gate, not a security boundary. Don't "fix" it.
- A failing check takes a **recovery checkpoint before rolling back**, so the
  rejected work is inspectable; `revert_on_failure: false` blocks without
  reverting. NOTE: `create` stores a diff + hash manifest, so untracked-file
  content is still lost to `clean -fd`.

## Do / Don't
- DO checkpoint before any `auto` stage and before any wide/blast-radius edit.
- DO call `record_tmux` once a pane exists — the checkpoint is taken before the
  harness starts, so pane IDs are attached afterwards.
- DON'T store secrets in checkpoints; reference env/paths only.
- (Future) SQLite is an OPTIONAL scaling path if file checkpoints get large — keep
  the file layout as the source of truth for v0.1–v0.2.
