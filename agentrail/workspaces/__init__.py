"""AgentRail workspaces subsystem — git worktree isolation per stage."""

from __future__ import annotations

from agentrail.workspaces.worktrees import (
    WorktreeError,
    WorktreeManager,
    branch_name,
    worktree_path,
)

__all__ = [
    "WorktreeError",
    "WorktreeManager",
    "branch_name",
    "worktree_path",
]
