"""File-based checkpoints — snapshot & rollback (no database).

A checkpoint captures enough state to restore a stage's worktree to a
known-good point ("local time travel"). Layout per checkpoints/CLAUDE.md::

    .agentrail/checkpoints/<stage-id>/<checkpoint-id>/
        git_head.txt     # worktree HEAD SHA
        changes.patch    # tracked + untracked diff
        manifest.json    # path -> content hash
        tmux.json        # recorded session/window/pane IDs

Rollback is ALWAYS scoped to a single worktree, never the main checkout.
The git runner is injected so the store is unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentrail.config import config_dir

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]

CHECKPOINTS_DIRNAME = "checkpoints"


class CheckpointError(RuntimeError):
    """Raised when a checkpoint or rollback git operation fails."""


def _default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - controlled args, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def checkpoints_dir(root: Path, stage_id: str) -> Path:
    return config_dir(root) / CHECKPOINTS_DIRNAME / stage_id


def _new_checkpoint_id() -> str:
    return datetime.now(UTC).strftime("cp-%Y%m%d%H%M%S%f")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class CheckpointStore:
    """Create and restore per-stage checkpoints inside a worktree."""

    repo_root: Path
    runner: Runner = _default_runner

    def _git(self, worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
        completed = self.runner(["git", "-C", str(worktree), *args], self.repo_root)
        if completed.returncode != 0:
            raise CheckpointError(
                f"git {' '.join(args)} failed ({completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed

    def create(
        self,
        stage_id: str,
        worktree: Path,
        *,
        tmux: dict[str, str] | None = None,
        checkpoint_id: str | None = None,
    ) -> str:
        """Snapshot ``worktree`` for ``stage_id``; return the checkpoint id."""

        cid = checkpoint_id or _new_checkpoint_id()
        cp_dir = checkpoints_dir(self.repo_root, stage_id) / cid
        cp_dir.mkdir(parents=True, exist_ok=True)

        head = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
        (cp_dir / "git_head.txt").write_text(head + "\n", encoding="utf-8")

        # Serialized diff of tracked changes against HEAD (side-effect free).
        # Untracked files are captured by hash in the manifest below.
        patch = self._git(worktree, "diff", "HEAD").stdout
        (cp_dir / "changes.patch").write_text(patch, encoding="utf-8")

        manifest = self._build_manifest(worktree)
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        (cp_dir / "tmux.json").write_text(
            json.dumps(tmux or {}, indent=2, sort_keys=True), encoding="utf-8"
        )
        return cid

    def _build_manifest(self, worktree: Path) -> dict[str, str]:
        listing = self._git(worktree, "ls-files", "--cached", "--others", "--exclude-standard")
        manifest: dict[str, str] = {}
        for rel in listing.stdout.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            target = worktree / rel
            if target.is_file():
                manifest[rel] = _hash_file(target)
        return manifest

    def read_head(self, stage_id: str, checkpoint_id: str) -> str:
        cp_dir = checkpoints_dir(self.repo_root, stage_id) / checkpoint_id
        head_file = cp_dir / "git_head.txt"
        if not head_file.exists():
            raise CheckpointError(f"no checkpoint {checkpoint_id} for stage {stage_id}")
        return head_file.read_text(encoding="utf-8").strip()

    def read_tmux(self, stage_id: str, checkpoint_id: str) -> dict[str, str]:
        cp_dir = checkpoints_dir(self.repo_root, stage_id) / checkpoint_id
        tmux_file = cp_dir / "tmux.json"
        if not tmux_file.exists():
            return {}
        data = json.loads(tmux_file.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}

    def list(self, stage_id: str) -> list[str]:
        base = checkpoints_dir(self.repo_root, stage_id)
        if not base.exists():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def rollback(self, stage_id: str, worktree: Path, checkpoint_id: str) -> str:
        """``git reset --hard <sha>`` + clean untracked, WITHIN this worktree.

        Returns the SHA the worktree was reset to. Never touches the main
        checkout.
        """

        head = self.read_head(stage_id, checkpoint_id)
        self._git(worktree, "reset", "--hard", head)
        # Delete untracked artifacts created after the checkpoint.
        self._git(worktree, "clean", "-fd")
        return head
