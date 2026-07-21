"""Checkpoints: real snapshot + worktree-scoped rollback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentrail.checkpoints import CheckpointError, CheckpointStore, checkpoints_dir
from agentrail.workspaces import WorktreeManager


def _worktree(repo: Path) -> Path:
    mgr = WorktreeManager(repo)
    ref = mgr.create("login", base="main")
    return Path(ref.path)


def test_create_writes_all_checkpoint_files(temp_git_repo: Path) -> None:
    wt = _worktree(temp_git_repo)
    store = CheckpointStore(temp_git_repo)
    cid = store.create("login", wt, tmux={"session": "agentrail-login", "pane": "%3"})

    cp_dir = checkpoints_dir(temp_git_repo, "login") / cid
    assert (cp_dir / "git_head.txt").read_text().strip()
    assert (cp_dir / "changes.patch").exists()
    manifest = json.loads((cp_dir / "manifest.json").read_text())
    assert "README.md" in manifest
    tmux = json.loads((cp_dir / "tmux.json").read_text())
    assert tmux["pane"] == "%3"


def test_rollback_restores_worktree_only(temp_git_repo: Path) -> None:
    wt = _worktree(temp_git_repo)
    store = CheckpointStore(temp_git_repo)
    cid = store.create("login", wt)

    # Make a mess in the worktree after the checkpoint.
    (wt / "README.md").write_text("corrupted\n", encoding="utf-8")
    (wt / "junk.txt").write_text("temp\n", encoding="utf-8")

    head = store.rollback("login", wt, cid)

    assert (wt / "README.md").read_text() == "# temp repo\n"
    assert not (wt / "junk.txt").exists()
    assert len(head) == 40  # full SHA


def test_list_and_read_head(temp_git_repo: Path) -> None:
    wt = _worktree(temp_git_repo)
    store = CheckpointStore(temp_git_repo)
    c1 = store.create("login", wt, checkpoint_id="cp-a")
    c2 = store.create("login", wt, checkpoint_id="cp-b")
    assert store.list("login") == ["cp-a", "cp-b"]
    assert store.read_head("login", c1) == store.read_head("login", c2)


def test_read_head_missing_raises(temp_git_repo: Path) -> None:
    store = CheckpointStore(temp_git_repo)
    with pytest.raises(CheckpointError):
        store.read_head("login", "nope")
