"""AgentRail git subsystem — branch/commit + stacked draft PRs.

Note: this package is ``agentrail.git`` and does NOT shadow GitPython; a bare
``import git`` still resolves to the third-party module.
"""

from __future__ import annotations

from agentrail.git.pr import (
    PullRequestError,
    PullRequestManager,
    base_branch_for,
    build_create_command,
    gh_authenticated,
    gh_available,
)
from agentrail.git.repo import GitError, GitRepo, git_available

__all__ = [
    "GitError",
    "GitRepo",
    "PullRequestError",
    "PullRequestManager",
    "base_branch_for",
    "build_create_command",
    "gh_authenticated",
    "gh_available",
    "git_available",
]
