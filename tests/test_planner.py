"""Planner + persistence: the README login/logout example and round-trips."""

from __future__ import annotations

from pathlib import Path

from agentrail.config import init_config
from agentrail.models import WorkflowStatus
from agentrail.workflow import load_workflow, plan_workflow, save_workflow

LOGIN_GOAL = "Implement login and logout as separate PRs"


def test_login_logout_example_matches_readme() -> None:
    workflow = plan_workflow(LOGIN_GOAL)

    ids = [s.id for s in workflow.stages]
    assert ids[0] == "analyse"
    assert set(ids) == {"analyse", "login", "logout"}
    assert workflow.status is WorkflowStatus.AWAITING_APPROVAL

    by_id = {s.id: s for s in workflow.stages}
    assert by_id["login"].depends_on == ["analyse"]
    assert by_id["logout"].depends_on == ["analyse"]
    assert by_id["analyse"].depends_on == []


def test_single_deliverable_goal_gets_implement_stage() -> None:
    workflow = plan_workflow("Add a healthcheck endpoint")
    ids = [s.id for s in workflow.stages]
    assert ids == ["analyse", "implement"]


def test_plan_persists_and_reloads(project_root: Path) -> None:
    init_config(project_root)
    workflow = plan_workflow(LOGIN_GOAL)
    path = save_workflow(project_root, workflow)

    assert path.exists()
    assert path == project_root / ".agentrail" / "workflow.yaml"

    reloaded = load_workflow(project_root)
    assert reloaded.goal == workflow.goal
    assert reloaded.status is WorkflowStatus.AWAITING_APPROVAL
    assert [s.id for s in reloaded.stages] == [s.id for s in workflow.stages]
