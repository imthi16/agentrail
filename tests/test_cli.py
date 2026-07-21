"""CLI smoke tests via Typer's runner, incl. event emission side effects."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentrail.cli import app
from agentrail.events import EventLog

runner = CliRunner()


@pytest.fixture()
def in_project(tmp_path: Path) -> Iterator[Path]:
    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(prev)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "agentrail" in result.stdout


def test_init_plan_status_flow(in_project: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (in_project / ".agentrail" / "config.yaml").exists()

    planned = runner.invoke(app, ["plan", "Implement login and logout as separate PRs"])
    assert planned.exit_code == 0
    assert (in_project / ".agentrail" / "workflow.yaml").exists()

    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0
    assert "awaiting_approval" in status.stdout


def test_plan_without_init_fails(in_project: Path) -> None:
    result = runner.invoke(app, ["plan", "do something"])
    assert result.exit_code == 1


def test_status_without_workflow_fails(in_project: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1


def test_commands_emit_events(in_project: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(app, ["plan", "Implement login and logout as separate PRs"])

    events = EventLog(in_project).read()
    types = [e.type for e in events]
    assert "project.initialized" in types
    assert "workflow.planned" in types

    planned = next(e for e in events if e.type == "workflow.planned")
    assert planned.attributes["stage_ids"] == ["analyse", "login", "logout"]


def _seed_workflow_with_pr(root: Path) -> None:
    """Persist a workflow whose login stage has a draft PR ref + criteria."""
    from agentrail.config import init_config
    from agentrail.models import PullRequestRef, Stage, Workflow
    from agentrail.workflow import save_workflow

    init_config(root)
    stage = Stage(
        id="login",
        title="Implement login",
        acceptance_criteria=["login works and is tested"],
        pull_request=PullRequestRef(number=None, base="main", head="stage/login"),
    )
    save_workflow(root, Workflow(goal="g", stages=[stage]))


def test_ready_dry_run_reports_without_promoting(in_project: Path) -> None:
    _seed_workflow_with_pr(in_project)
    result = runner.invoke(app, ["ready", "login", "--checks-passed", "--dry-run"])
    assert result.exit_code == 0
    # number is None (degraded create) so it stays a draft; command still succeeds.
    assert "login" in result.stdout


def test_ready_blocks_when_checks_failed(in_project: Path) -> None:
    _seed_workflow_with_pr(in_project)
    result = runner.invoke(app, ["ready", "login", "--checks-failed"])
    assert result.exit_code == 0  # command runs; gate blocks the stage
    assert "blocked" in result.stdout.lower()
    types = [e.type for e in EventLog(in_project).read()]
    assert "pr.ready" not in types  # nothing promoted


def test_ready_without_pr_stages_exits_nonzero(in_project: Path) -> None:
    from agentrail.config import init_config
    from agentrail.models import Stage, Workflow
    from agentrail.workflow import save_workflow

    init_config(in_project)
    save_workflow(in_project, Workflow(goal="g", stages=[Stage(id="a", title="A")]))
    result = runner.invoke(app, ["ready"])
    assert result.exit_code == 1
