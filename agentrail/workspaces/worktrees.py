"""Git worktree manager — one stage = one worktree = one branch.

Wraps the verified ``git worktree`` commands from workspaces/CLAUDE.md. Each
stage gets ``.agentrail/worktrees/<stage-id>`` on branch ``stage/<stage-id>``.
The subprocess runner is injected so the manager is unit-testable, and paths are
resolved against the worktree root (never the main repo).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentrail.config import config_dir
from agentrail.models import WorktreeRef

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]

WORKTREES_DIRNAME = "worktrees"


class WorktreeError(RuntimeError):
    """Raised when a git worktree command fails."""


def _default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - args are controlled, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def branch_name(stage_id: str) -> str:
    return f"stage/{stage_id}"


def worktree_path(root: Path, stage_id: str) -> Path:
    return config_dir(root) / WORKTREES_DIRNAME / stage_id


@dataclass
class WorktreeManager:
    """Create/list/remove git worktrees for stages under ``.agentrail``."""

    repo_root: Path
    runner: Runner = _default_runner

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        completed = self.runner(["git", *args], self.repo_root)
        if completed.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed

    def create(self, stage_id: str, base: str) -> WorktreeRef:
        """Create ``stage/<id>`` at ``.agentrail/worktrees/<id>`` from ``base``."""

        path = worktree_path(self.repo_root, stage_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(stage_id)
        self._git("worktree", "add", "-b", branch, str(path), base)
        return WorktreeRef(path=str(path), branch=branch, base=base)

    def list(self) -> list[dict[str, str]]:
        """Parse ``git worktree list --porcelain`` into a list of records."""

        completed = self._git("worktree", "list", "--porcelain")
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if not line.strip():
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            records.append(current)
        return records

    def remove(self, stage_id: str, *, force: bool = False) -> None:
        """Remove a stage's worktree (files + metadata, not history)."""

        path = worktree_path(self.repo_root, stage_id)
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        self._git(*args)

    def prune(self) -> None:
        """Prune stale worktree metadata."""

        self._git("worktree", "prune")

    def resolve_in_worktree(self, stage_id: str, relative: str) -> Path:
        """Resolve ``relative`` against the WORKTREE root, not the main repo."""

        base = worktree_path(self.repo_root, stage_id).resolve()
        return (base / relative).resolve()
