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
- **Post-edit:** run LSP diagnostics, formatters, and targeted tests.
- **Blast-radius check:** if an edit introduces a syntax error, a type collision
  outside `allowed_paths`, or fails tests → auto-revert from the last checkpoint
  patch and retry the edit with the error log attached to the prompt context.

## Do / Don't
- DO checkpoint before any `auto` stage and before any wide/blast-radius edit.
- DON'T store secrets in checkpoints; reference env/paths only.
- (Future) SQLite is an OPTIONAL scaling path if file checkpoints get large — keep
  the file layout as the source of truth for v0.1–v0.2.
