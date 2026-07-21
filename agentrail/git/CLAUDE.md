# agentrail/git/ — Branches, Commits, Stacked Draft PRs

Owns branch/commit operations and pull-request orchestration via the GitHub CLI
(`gh`). Each completed stage → its own **draft** PR. Dependent stages → **stacked**
PRs.

> Import caveat: this package is `agentrail.git`. It does NOT shadow the
> third-party `git` module (GitPython) — a bare `import git` still resolves to
> GitPython. Inside this package, import GitPython as `import git` normally; refer
> to siblings via `from agentrail import git as ar_git` if disambiguation is ever
> needed.

## Verified gh commands
- Draft PR into the default branch:
  `gh pr create --base main --head stage/<id> --title "<t>" --body "<b>" --draft`
- Stacked draft PR (child base = parent stage head branch):
  `gh pr create --base stage/<parent> --head stage/<child> --title "<t>" --draft`
- Promote draft → ready: `gh pr ready <number>` (revert with `--undo`)
- Useful flags: `-B/--base` (branch to merge INTO), `-H/--head`, `-d/--draft`,
  `-t/--title`, `-b/--body`, `-f/--fill`, `-r/--reviewer`, `-l/--label`, `-w/--web`.
- Case matters: `-B` = `--base` (uppercase), `-b` = `--body` (lowercase).

## Conventions
- Push the branch (`git push -u origin stage/<id>`) BEFORE `gh pr create`.
- Record PR number + base + head in `.agentrail/workflow.yaml` under the stage.
- Stacked-PR maintenance: when the parent PR merges/squashes, re-target children
  (update base or merge parent into child) before marking them ready.
- Prefix PR titles with `[AgentRail] <Stage Name>` for traceability.

## Do / Don't
- DO open PRs as drafts until the stage's acceptance criteria + checks pass.
- DO one PR per stage — never conflate multiple stages into one commit/PR.
- DON'T set a child PR's base to `main` when it depends on a parent stage.
