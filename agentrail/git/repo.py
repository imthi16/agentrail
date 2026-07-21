"""Git branch/commit operations for stages (raw git via injected runner).

Thin, testable wrapper for the per-stage git actions the control plane needs:
staging, committing, pushing, and querying the head SHA of a stage branch. PR
orchestration lives in :mod:`agentrail.git.pr`.

Note: this package is ``agentrail.git``; a bare ``import git`` still resolves to
GitPython. We use the plain ``git`` CLI here via an injected runner so the code
stays unit-testable and dependency-light.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class GitError(RuntimeError):
    """Raised when a git command fails."""


def git_available() -> bool:
    return shutil.which("git") is not None


def _default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - controlled args, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass
class GitRepo:
    """Per-worktree git operations rooted at ``cwd``."""

    cwd: Path
    runner: Runner = _default_runner

    def _git(self, *args: str) -> str:
        completed = self.runner(["git", *args], self.cwd)
        if completed.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed.stdout

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def head_sha(self, ref: str = "HEAD") -> str:
        return self._git("rev-parse", ref).strip()

    def stage_all(self) -> None:
        self._git("add", "-A")

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain").strip())

    def commit(self, message: str, *, allow_empty: bool = False) -> str:
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self._git(*args)
        return self.head_sha()

    def has_remote(self, name: str = "origin") -> bool:
        try:
            remotes = self._git("remote").split()
        except GitError:
            return False
        return name in remotes

    def push(self, branch: str, *, remote: str = "origin", set_upstream: bool = True) -> None:
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args += [remote, branch]
        self._git(*args)
