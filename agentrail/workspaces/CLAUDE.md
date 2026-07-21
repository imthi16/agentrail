# agentrail/workspaces/ — Worktree Isolation (+ Docker / remote, future)

Owns the workspace lifecycle for per-stage isolation. **One stage = one worktree =
one branch.** Git worktrees are the v0.1 backend; Docker sandboxes and remote
runners are v0.3 and share this interface.

## Layout
- Worktree path: `.agentrail/worktrees/<stage-id>/` (gitignored).
- Branch name: `stage/<stage-id>`.

## Verified git worktree commands
- Create branch + worktree:
  `git worktree add -b stage/<id> .agentrail/worktrees/<id> <base>`
- Create from an existing branch:
  `git worktree add .agentrail/worktrees/<id> <branch>`
- List (machine-readable): `git worktree list --porcelain`
- Remove: `git worktree remove .agentrail/worktrees/<id>` (add `--force` if dirty)
- Prune stale metadata: `git worktree prune`
- Lock a protected worktree: `git worktree lock --reason "<why>" .agentrail/worktrees/<id>`

## Isolation semantics (important)
- All worktrees share ONE object store and `refs/`; but HEAD, index, and working
  tree are per-worktree. The shared git dir is reachable via `--git-common-dir`.
- The SAME branch cannot be checked out in two worktrees simultaneously.
- Removing a worktree deletes files/metadata, NOT commit history.
- When resolving paths for policy checks, resolve against the worktree root, not
  the main repo (a known Claude Code edge case shows main-repo paths leaking into
  prompts — resolve explicitly).

## Conventions
- Record each worktree path + branch + base in `.agentrail/workflow.yaml` under
  the stage.
- Each stage mutates ONLY its own worktree; never share a checkout between stages.
- Tear down the worktree only after its PR is opened and the checkpoint is saved.

## Do / Don't
- DO create the worktree from the correct base (parent stage head for stacked
  work, else the default branch).
- DON'T check out `main` (or any shared branch) inside a stage worktree.
