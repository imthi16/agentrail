"""Worktree manager: real git-repo create/list/remove/prune round-trips."""

from __future__ import annotations

from pathlib import Path

from agentrail.workspaces import WorktreeManager, branch_name, worktree_path


def test_create_makes_branch_and_worktree(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    ref = mgr.create("login", base="main")

    expected = worktree_path(temp_git_repo, "login")
    assert Path(ref.path) == expected
    assert expected.is_dir()
    assert ref.branch == "stage/login"
    assert ref.base == "main"
    # The seed commit's file is present in the isolated worktree.
    assert (expected / "README.md").exists()


def test_list_reports_created_worktree(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    mgr.create("login", base="main")
    branches = {rec.get("branch", "") for rec in mgr.list()}
    assert f"refs/heads/{branch_name('login')}" in branches


def test_remove_then_prune(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    mgr.create("login", base="main")
    mgr.remove("login")
    assert not worktree_path(temp_git_repo, "login").exists()
    mgr.prune()  # must not raise


def test_two_independent_stage_worktrees(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    login = mgr.create("login", base="main")
    logout = mgr.create("logout", base="main")

    assert login.path != logout.path
    assert {login.branch, logout.branch} == {"stage/login", "stage/logout"}
    assert Path(login.path).is_dir()
    assert Path(logout.path).is_dir()


def test_resolve_in_worktree_stays_in_worktree(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    mgr.create("login", base="main")
    resolved = mgr.resolve_in_worktree("login", "src/app.py")
    assert resolved.is_relative_to(worktree_path(temp_git_repo, "login").resolve())


def test_create_is_idempotent_when_worktree_exists(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    first = mgr.create("login", base="main")
    again = mgr.create("login", base="main")  # must not raise
    assert first.path == again.path
    assert first.branch == again.branch


def test_create_recovers_when_only_branch_exists(temp_git_repo: Path) -> None:
    mgr = WorktreeManager(temp_git_repo)
    # Simulate a prior run that left the branch but no worktree.
    mgr.create("login", base="main")
    mgr.remove("login")
    assert mgr.branch_exists("stage/login") is True
    assert not worktree_path(temp_git_repo, "login").exists()

    recovered = mgr.create("login", base="main")  # attaches to existing branch
    assert recovered.branch == "stage/login"
    assert worktree_path(temp_git_repo, "login").is_dir()
