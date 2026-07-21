"""Stacked draft PR orchestration via the GitHub CLI (``gh``).

Each completed stage gets its own **draft** PR; dependent stages become
**stacked** PRs whose base is the parent stage's head branch (never ``main``).
Verified command shapes come from git/CLAUDE.md.

Graceful degradation: if ``gh`` is missing, unauthenticated, or there is no
remote, callers pass ``dry_run=True`` (or the planner sets it automatically) and
this module returns the exact command it WOULD run without executing it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentrail.models import PullRequestRef, Stage, Workflow
from agentrail.workspaces import branch_name

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]

TITLE_PREFIX = "[AgentRail]"


class PullRequestError(RuntimeError):
    """Raised when a gh PR command fails."""


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_authenticated(runner: Runner | None = None, cwd: Path | None = None) -> bool:
    """Feature-detect gh auth without raising."""

    if not gh_available():
        return False
    run = runner or _default_runner
    completed = run(["gh", "auth", "status"], cwd or Path.cwd())
    return completed.returncode == 0


def _default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - controlled args, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def base_branch_for(stage: Stage, workflow: Workflow, default_base: str) -> str:
    """Stacked-PR base: the (single) parent stage head branch, else default.

    If a stage depends on exactly one parent, its PR base is that parent's
    ``stage/<parent>`` branch. With zero or multiple parents we fall back to the
    default base (typically ``main``); multi-parent stacking is a v0.3 concern.
    """

    parents = [dep for dep in stage.depends_on if any(s.id == dep for s in workflow.stages)]
    if len(parents) == 1:
        return branch_name(parents[0])
    return default_base


def build_create_command(
    stage: Stage,
    *,
    base: str,
    head: str,
    body: str = "",
) -> list[str]:
    """Assemble the verified ``gh pr create --draft`` argv for a stage."""

    title = f"{TITLE_PREFIX} {stage.title}"
    args = [
        "gh",
        "pr",
        "create",
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--draft",
    ]
    if body:
        args += ["--body", body]
    return args


@dataclass
class PullRequestManager:
    """Creates stacked draft PRs, with a dry-run path for degraded envs."""

    repo_root: Path
    default_base: str = "main"
    runner: Runner = _default_runner

    def create_for_stage(
        self,
        stage: Stage,
        workflow: Workflow,
        *,
        dry_run: bool = False,
        body: str = "",
    ) -> PullRequestRef:
        """Open (or simulate) a draft PR for ``stage``; return its ref.

        Records base/head so the ref can be persisted on the stage. On dry-run
        or when gh is unavailable, ``number``/``url`` stay ``None`` and no
        process is spawned.
        """

        head = branch_name(stage.id)
        base = base_branch_for(stage, workflow, self.default_base)
        argv = build_create_command(stage, base=base, head=head, body=body)

        if dry_run or not gh_authenticated(self.runner, self.repo_root):
            return PullRequestRef(number=None, base=base, head=head, url=None, draft=True)

        completed = self.runner(argv, self.repo_root)
        if completed.returncode != 0:
            raise PullRequestError(
                f"gh pr create failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        url = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else None
        number = _parse_pr_number(url) if url else None
        return PullRequestRef(number=number, base=base, head=head, url=url, draft=True)

    def mark_ready(
        self,
        pr: PullRequestRef,
        stage: Stage,
        *,
        checks_passed: bool,
        dry_run: bool = False,
    ) -> PullRequestRef:
        """Promote a draft PR to ready ONLY when the merge gate is satisfied.

        Gate = acceptance criteria present AND ``checks_passed`` (CI/quality
        gates). Fails closed: an unmet gate leaves the PR a draft and raises.
        On dry-run or when gh is unavailable the ref is returned unchanged.
        """

        if not self._merge_gate_ok(stage, checks_passed):
            raise PullRequestError(
                f"merge gate not satisfied for stage {stage.id!r}: "
                f"checks_passed={checks_passed}, "
                f"acceptance_criteria={len(stage.acceptance_criteria)}"
            )
        if pr.number is None:
            # Nothing to promote (dry-run / degraded create): return as-is.
            return pr
        if dry_run or not gh_authenticated(self.runner, self.repo_root):
            return pr

        completed = self.runner(["gh", "pr", "ready", str(pr.number)], self.repo_root)
        if completed.returncode != 0:
            raise PullRequestError(
                f"gh pr ready failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return pr.model_copy(update={"draft": False})

    @staticmethod
    def _merge_gate_ok(stage: Stage, checks_passed: bool) -> bool:
        return checks_passed and bool(stage.acceptance_criteria)


def _parse_pr_number(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None
