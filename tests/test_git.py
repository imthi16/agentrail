"""Git repo ops (real) + stacked draft PR orchestration (fake runner)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentrail.git import (
    GitRepo,
    PullRequestError,
    PullRequestManager,
    base_branch_for,
    build_create_command,
)
from agentrail.models import PullRequestRef, Stage, Workflow


# --- GitRepo (real temp repo) ----------------------------------------------
def test_commit_and_head_sha(temp_git_repo: Path) -> None:
    repo = GitRepo(temp_git_repo)
    assert repo.current_branch() == "main"
    (temp_git_repo / "new.txt").write_text("hi\n", encoding="utf-8")
    assert repo.is_dirty() is True
    repo.stage_all()
    sha = repo.commit("add new.txt")
    assert len(sha) == 40
    assert repo.is_dirty() is False


def test_has_remote_false_on_fresh_repo(temp_git_repo: Path) -> None:
    assert GitRepo(temp_git_repo).has_remote() is False


# --- PR command shape + stacking (no network) ------------------------------
def _login_logout() -> Workflow:
    return Workflow(
        goal="Implement login and logout as separate PRs",
        stages=[
            Stage(id="analyse", title="Analyse"),
            Stage(id="login", title="Implement login", depends_on=["analyse"]),
            Stage(id="logout", title="Implement logout", depends_on=["analyse"]),
        ],
    )


def test_build_create_command_is_verified_shape() -> None:
    stage = Stage(id="login", title="Implement login", depends_on=["analyse"])
    argv = build_create_command(stage, base="stage/analyse", head="stage/login")
    assert argv[:3] == ["gh", "pr", "create"]
    assert "--draft" in argv
    assert argv[argv.index("--base") + 1] == "stage/analyse"
    assert argv[argv.index("--head") + 1] == "stage/login"
    title = argv[argv.index("--title") + 1]
    assert title == "[AgentRail] Implement login"


def test_stacked_base_is_parent_head_branch() -> None:
    wf = _login_logout()
    login = next(s for s in wf.stages if s.id == "login")
    analyse = next(s for s in wf.stages if s.id == "analyse")
    assert base_branch_for(login, wf, "main") == "stage/analyse"
    assert base_branch_for(analyse, wf, "main") == "main"  # no parent -> default


def test_dry_run_does_not_invoke_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def spy(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    wf = _login_logout()
    login = next(s for s in wf.stages if s.id == "login")
    mgr = PullRequestManager(tmp_path, runner=spy)
    ref = mgr.create_for_stage(login, wf, dry_run=True)

    assert calls == []  # nothing executed
    assert ref.number is None
    assert ref.base == "stage/analyse"
    assert ref.head == "stage/login"
    assert ref.draft is True


def test_create_parses_pr_number_from_url(tmp_path: Path) -> None:
    def fake(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["gh", "auth"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "https://github.com/acme/app/pull/42\n", "")

    wf = _login_logout()
    login = next(s for s in wf.stages if s.id == "login")
    mgr = PullRequestManager(tmp_path, runner=fake)
    ref = mgr.create_for_stage(login, wf)
    assert ref.number == 42
    assert ref.url == "https://github.com/acme/app/pull/42"


def test_create_degrades_when_unauthenticated(tmp_path: Path) -> None:
    def unauth(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["gh", "auth"]:
            return subprocess.CompletedProcess(args, 1, "", "not logged in")
        raise AssertionError("gh pr create must not run when unauthenticated")

    wf = _login_logout()
    logout = next(s for s in wf.stages if s.id == "logout")
    mgr = PullRequestManager(tmp_path, runner=unauth)
    ref = mgr.create_for_stage(logout, wf)
    assert ref.number is None
    assert ref.base == "stage/analyse"


# --- Merge gate (gh pr ready) ----------------------------------------------
def _pr_ready_stage() -> Stage:
    return Stage(
        id="login",
        title="Implement login",
        acceptance_criteria=["login works and is tested"],
    )


def test_mark_ready_blocked_when_checks_fail(tmp_path: Path) -> None:
    pr = PullRequestRef(number=7, base="main", head="stage/login")
    mgr = PullRequestManager(tmp_path)
    with pytest.raises(PullRequestError):
        mgr.mark_ready(pr, _pr_ready_stage(), checks_passed=False)


def test_mark_ready_blocked_without_acceptance_criteria(tmp_path: Path) -> None:
    pr = PullRequestRef(number=7, base="main", head="stage/login")
    bare = Stage(id="login", title="Login")  # no acceptance criteria
    mgr = PullRequestManager(tmp_path)
    with pytest.raises(PullRequestError):
        mgr.mark_ready(pr, bare, checks_passed=True)


def test_mark_ready_promotes_when_gate_passes(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    pr = PullRequestRef(number=7, base="main", head="stage/login")
    mgr = PullRequestManager(tmp_path, runner=fake)
    result = mgr.mark_ready(pr, _pr_ready_stage(), checks_passed=True)

    assert result.draft is False
    assert ["gh", "pr", "ready", "7"] in calls


def test_mark_ready_dry_run_does_not_invoke(tmp_path: Path) -> None:
    def spy(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry-run must not invoke gh")

    pr = PullRequestRef(number=7, base="main", head="stage/login")
    mgr = PullRequestManager(tmp_path, runner=spy)
    result = mgr.mark_ready(pr, _pr_ready_stage(), checks_passed=True, dry_run=True)
    assert result.draft is True  # unchanged
