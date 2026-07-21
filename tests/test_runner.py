"""Workflow runner: dry-run plan + real per-stage execution on a temp repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrail.config import init_config
from agentrail.events import EventLog
from agentrail.models import Mode, WorkflowStatus
from agentrail.runner import WorkflowRunner
from agentrail.workflow import plan_workflow, save_workflow
from agentrail.workspaces import worktree_path

GOAL = "Implement login and logout as separate PRs"


class FakeAdapter:
    """A no-op adapter so runner tests never spawn a real harness."""

    name = "fake"

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        return ["fake", "run", prompt]

    def start(
        self,
        prompt: str,
        *,
        mode: Mode,
        model_id: str | None,
        cwd: Path,
        dry_run: bool = False,
    ) -> object:
        from agentrail.adapters import SessionHandle

        return SessionHandle(adapter=self.name, name="fake-run", cwd=cwd, started=True)


def test_run_requires_approval(project_root: Path) -> None:
    init_config(project_root)
    workflow = plan_workflow(GOAL)  # awaiting_approval
    runner = WorkflowRunner(project_root)
    with pytest.raises(RuntimeError):
        runner.run(workflow)


def test_dry_run_plans_stacked_prs_without_side_effects(project_root: Path) -> None:
    init_config(project_root)
    workflow = plan_workflow(GOAL)
    workflow.status = WorkflowStatus.APPROVED
    runner = WorkflowRunner(project_root)

    report = runner.run(workflow, dry_run=True)

    assert report.dry_run is True
    ids = [p.stage_id for p in report.stages]
    assert ids[0] == "analyse"
    assert set(ids) == {"analyse", "login", "logout"}

    by_id = {p.stage_id: p for p in report.stages}
    # analyse roots at main; login/logout stack on analyse's head branch.
    assert by_id["analyse"].pr_base == "main"
    assert by_id["login"].pr_base == "stage/analyse"
    assert by_id["logout"].pr_base == "stage/analyse"
    assert by_id["login"].pr_head == "stage/login"

    # No worktrees created on a dry-run.
    assert not worktree_path(project_root, "login").exists()


def test_dry_run_emits_run_events(project_root: Path) -> None:
    init_config(project_root)
    workflow = plan_workflow(GOAL)
    workflow.status = WorkflowStatus.APPROVED
    WorkflowRunner(project_root).run(workflow, dry_run=True)

    types = [e.type for e in EventLog(project_root).read()]
    assert "workflow.run_started" in types
    assert types.count("stage.started") == 3
    assert "workflow.run_finished" in types


def test_run_started_records_budget_from_config(project_root: Path) -> None:
    from agentrail.config import Budget, Config, write_config

    write_config(project_root, Config(budget=Budget(max_usd=12.5, max_tokens=900_000)))
    workflow = plan_workflow(GOAL)
    workflow.status = WorkflowStatus.APPROVED
    WorkflowRunner(project_root).run(workflow, dry_run=True)

    started = next(e for e in EventLog(project_root).read() if e.type == "workflow.run_started")
    assert started.attributes["budget_max_usd"] == 12.5
    assert started.attributes["budget_max_tokens"] == 900_000


def test_real_run_creates_worktrees_and_checkpoints(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = plan_workflow(GOAL)
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(temp_git_repo, adapter=FakeAdapter())  # type: ignore[arg-type]
    report = runner.run(workflow, dry_run=False)

    assert workflow.status is WorkflowStatus.COMPLETED
    for plan in report.stages:
        assert plan.performed is True
        assert worktree_path(temp_git_repo, plan.stage_id).is_dir()
        assert plan.checkpoint_id is not None

    # Stacked bases hold on the real run too.
    by_id = {p.stage_id: p for p in report.stages}
    assert by_id["login"].worktree_base == "stage/analyse"
