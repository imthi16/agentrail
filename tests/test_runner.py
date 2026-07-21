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


def test_auto_mode_blocked_without_intent_lock(temp_git_repo: Path) -> None:
    from agentrail.models import Mode
    from agentrail.runner import StageBlockedError

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")  # analyse -> implement
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(
        temp_git_repo,
        mode=Mode.AUTO,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    with pytest.raises(StageBlockedError):
        runner.run(workflow, dry_run=False)

    # A stage.blocked event is recorded with the reason.
    blocked = [e for e in EventLog(temp_git_repo).read() if e.type == "stage.blocked"]
    assert blocked
    assert "Intent Lock" in blocked[0].attributes["reason"]


def test_auto_mode_admitted_with_bound_lock(temp_git_repo: Path) -> None:
    from agentrail.models import IntentLock, Mode
    from agentrail.policies import bind_lock_to_stage

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    # Bind an Intent Lock (with allowed_paths) to every stage.
    for stage in workflow.stages:
        bind_lock_to_stage(temp_git_repo, stage, IntentLock(allowed_paths=["src"]))
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(
        temp_git_repo,
        mode=Mode.AUTO,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    report = runner.run(workflow, dry_run=False)
    assert workflow.status is WorkflowStatus.COMPLETED
    assert all(p.performed for p in report.stages)


def test_tampered_lock_blocks_stage(temp_git_repo: Path) -> None:
    import yaml

    from agentrail.models import IntentLock
    from agentrail.policies import bind_lock_to_stage, lock_path
    from agentrail.runner import StageBlockedError

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    stage = workflow.stages[0]
    bind_lock_to_stage(temp_git_repo, stage, IntentLock(allowed_paths=["src"]))
    save_workflow(temp_git_repo, workflow)

    # Tamper with the on-disk lock after the hash was recorded.
    path = lock_path(temp_git_repo, stage.id)
    data = yaml.safe_load(path.read_text())
    data["allowed_paths"].append("/etc")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    runner = WorkflowRunner(temp_git_repo, adapter=FakeAdapter())  # type: ignore[arg-type]
    with pytest.raises(StageBlockedError):
        runner.run(workflow, dry_run=False)


def test_run_emits_model_calls_with_routed_tiers(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")  # analyse -> implement
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    WorkflowRunner(temp_git_repo, adapter=FakeAdapter()).run(workflow)  # type: ignore[arg-type]

    calls = [e for e in EventLog(temp_git_repo).read() if e.type == "model.call"]
    by_stage = {e.stage_id: e.attributes for e in calls}
    # analyse routes Deep; implement routes Balanced (auto-downgrade policy).
    assert by_stage["analyse"]["tier"] == "deep"
    assert by_stage["implement"]["tier"] == "balanced"
    assert by_stage["analyse"]["model_id"] == "claude-opus-4-8"


def test_run_blocks_when_budget_exceeded(temp_git_repo: Path) -> None:
    from agentrail.config import Budget, Config, write_config
    from agentrail.runner import StageBlockedError

    # A microscopic token budget guarantees the first stage's call is refused.
    write_config(temp_git_repo, Config(budget=Budget(max_tokens=1)))
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(temp_git_repo, adapter=FakeAdapter())  # type: ignore[arg-type]
    with pytest.raises(StageBlockedError):
        runner.run(workflow)

    types = [e.type for e in EventLog(temp_git_repo).read()]
    assert "budget.exceeded" in types


def test_edit_guard_passing_allows_completion(temp_git_repo: Path) -> None:
    from agentrail.checkpoints import SemanticEditGuard

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    guard = SemanticEditGuard(checks=[lambda _p: (True, "")])
    runner = WorkflowRunner(
        temp_git_repo,
        adapter=FakeAdapter(),
        edit_guard=guard,  # type: ignore[arg-type]
    )
    report = runner.run(workflow)
    assert all(p.performed for p in report.stages)


def test_edit_guard_failure_reverts_and_blocks(temp_git_repo: Path) -> None:
    from agentrail.checkpoints import SemanticEditGuard
    from agentrail.runner import StageBlockedError

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    guard = SemanticEditGuard(checks=[lambda _p: (False, "syntax error introduced")])
    runner = WorkflowRunner(
        temp_git_repo,
        adapter=FakeAdapter(),
        edit_guard=guard,  # type: ignore[arg-type]
    )
    with pytest.raises(StageBlockedError):
        runner.run(workflow)

    types = [e.type for e in EventLog(temp_git_repo).read()]
    assert "stage.guard_reverted" in types


class FailingAdapter:
    """Adapter whose harness never starts (missing binary / nonzero exit)."""

    name = "failing"

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        return ["failing", "run", prompt]

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

        return SessionHandle(adapter=self.name, name="failing-run", cwd=cwd, started=False)


def test_plan_mode_denies_harness_session(temp_git_repo: Path) -> None:
    from agentrail.models import Mode
    from agentrail.runner import StageBlockedError

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(
        temp_git_repo,
        mode=Mode.PLAN,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    with pytest.raises(StageBlockedError):
        runner.run(workflow)

    types = [e.type for e in EventLog(temp_git_repo).read()]
    assert "policy.session_authorized" in types
    assert "stage.blocked" in types


def test_harness_failure_marks_stage_failed(temp_git_repo: Path) -> None:
    from agentrail.models import StageStatus
    from agentrail.runner import StageBlockedError

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(temp_git_repo, adapter=FailingAdapter())  # type: ignore[arg-type]
    with pytest.raises(StageBlockedError):
        runner.run(workflow)

    # First stage must NOT be reported completed when the harness never started.
    assert workflow.stages[0].status is StageStatus.FAILED
    types = [e.type for e in EventLog(temp_git_repo).read()]
    assert "stage.failed" in types
    assert "stage.completed" not in types


def test_stage_changes_are_committed_into_the_branch(temp_git_repo: Path) -> None:
    """A stage that edits its worktree gets those edits committed on its branch."""
    from agentrail.adapters import SessionHandle
    from agentrail.git import GitRepo
    from agentrail.workspaces import worktree_path

    class EditingAdapter:
        name = "editing"

        def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
            return ["editing"]

        def start(self, prompt, *, mode, model_id, cwd, dry_run=False):  # type: ignore[no-untyped-def]
            (Path(cwd) / "feature.txt").write_text("implemented\n", encoding="utf-8")
            return SessionHandle(adapter=self.name, name="e", cwd=cwd, started=True)

    init_config(temp_git_repo)
    workflow = plan_workflow("Add a healthcheck endpoint")
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(temp_git_repo, adapter=EditingAdapter())  # type: ignore[arg-type]
    runner.run(workflow)

    # The edit is committed on the stage worktree (clean tree, file tracked).
    wt = worktree_path(temp_git_repo, "analyse")
    repo = GitRepo(wt)
    assert repo.is_dirty() is False
    assert (wt / "feature.txt").exists()
    log = repo._git("log", "--oneline", "-1")
    assert "[AgentRail]" in log


def test_resume_skips_completed_stages(temp_git_repo: Path) -> None:
    from agentrail.models import StageStatus

    init_config(temp_git_repo)
    workflow = plan_workflow(GOAL)  # analyse -> login, logout
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(temp_git_repo, workflow)

    runner = WorkflowRunner(temp_git_repo, adapter=FakeAdapter())  # type: ignore[arg-type]
    runner.run(workflow)  # full run: every stage + branch now exists
    assert all(s.status is StageStatus.COMPLETED for s in workflow.stages)

    # Re-approve (as `resume` would) and re-run: all stages already completed,
    # so every one is skipped, not redone.
    workflow.status = WorkflowStatus.APPROVED
    report = runner.run(workflow)
    assert report.stages == []  # nothing re-executed
    skipped = [e for e in EventLog(temp_git_repo).read() if e.type == "stage.skipped"]
    assert {e.stage_id for e in skipped} >= {"analyse", "login", "logout"}
