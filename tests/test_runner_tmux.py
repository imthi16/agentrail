"""Runner <-> tmux supervision wiring, fully offline via an injected executor.

No test here needs a tmux server: the executor is the seam that keeps the run
loop's behaviour identical on machines with and without tmux.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agentrail.adapters import Capabilities, SessionHandle
from agentrail.checkpoints import CheckpointStore
from agentrail.config import Config, TmuxSettings, init_config, write_config
from agentrail.events import EventLog
from agentrail.models import Mode, StageStatus, WorkflowStatus
from agentrail.runner import StageBlockedError, WorkflowRunner
from agentrail.tmux import ExecResult, PaneHandle
from agentrail.workflow import plan_workflow, save_workflow
from agentrail.workspaces import worktree_path

GOAL = "Add a healthcheck endpoint"


@dataclass
class RecordingExecutor:
    """Captures what the runner asked to be supervised."""

    exit_code: int | None = 0
    timed_out: bool = False
    writes_file: str | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def run_argv(
        self,
        *,
        stage_id: str,
        adapter_name: str,
        argv: list[str],
        cwd: Path,
        attempt: str,
    ) -> ExecResult:
        self.calls.append({"stage_id": stage_id, "adapter": adapter_name, "argv": argv, "cwd": cwd})
        if self.writes_file is not None:
            (cwd / self.writes_file).write_text("done\n", encoding="utf-8")
        handle = SessionHandle(
            adapter=adapter_name,
            name=f"{adapter_name}-{stage_id}",
            cwd=cwd,
            argv=argv,
            started=(not self.timed_out and self.exit_code == 0),
            output="harness output\n",
        )
        return ExecResult(
            handle=handle,
            pane=PaneHandle(session_id="$1", window_id="@2", pane_id="%3"),
            exit_code=self.exit_code,
            timed_out=self.timed_out,
        )


class ProcessAdapter:
    """A process-backed adapter: eligible for supervision."""

    name = "proc"

    def available(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities(non_interactive=True, process_backed=True, edits_files=True)

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        return ["proc", "run", prompt]

    def start(self, prompt, *, mode, model_id, cwd, dry_run=False):  # type: ignore[no-untyped-def]
        raise AssertionError("a supervised adapter must not be started directly")


class ApiAdapter:
    """An API-backed adapter: an HTTP call gains nothing from a pane."""

    name = "api"

    def available(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities(non_interactive=True, process_backed=False, edits_files=False)

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        return ["POST /chat/completions"]

    def start(self, prompt, *, mode, model_id, cwd, dry_run=False):  # type: ignore[no-untyped-def]
        return SessionHandle(adapter=self.name, name="api", cwd=cwd, started=True)


def _approved(root: Path):  # type: ignore[no-untyped-def]
    workflow = plan_workflow(GOAL)
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(root, workflow)
    return workflow


def test_process_backed_adapter_runs_in_a_supervised_pane(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    executor = RecordingExecutor(writes_file="feature.txt")

    runner = WorkflowRunner(
        temp_git_repo,
        adapter=ProcessAdapter(),  # type: ignore[arg-type]
        executor=executor,
    )
    report = runner.run(workflow)

    assert executor.calls, "the harness must go through the executor, not a bare subprocess"
    call = executor.calls[0]
    assert call["adapter"] == "proc"
    assert call["cwd"] == worktree_path(temp_git_repo, "analyse")
    assert all(plan.ran_in_tmux for plan in report.stages)


def test_pane_ids_are_recorded_in_the_checkpoint(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    runner = WorkflowRunner(
        temp_git_repo,
        adapter=ProcessAdapter(),  # type: ignore[arg-type]
        executor=RecordingExecutor(writes_file="feature.txt"),
    )
    report = runner.run(workflow)

    plan = report.stages[0]
    assert plan.checkpoint_id is not None
    recorded = CheckpointStore(temp_git_repo).read_tmux(plan.stage_id, plan.checkpoint_id)
    assert recorded == {"session_id": "$1", "window_id": "@2", "pane_id": "%3"}


def test_api_backed_adapter_is_never_supervised(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    executor = RecordingExecutor()

    runner = WorkflowRunner(
        temp_git_repo,
        adapter=ApiAdapter(),  # type: ignore[arg-type]
        executor=executor,
    )
    report = runner.run(workflow)

    assert executor.calls == []
    assert not any(plan.ran_in_tmux for plan in report.stages)


def test_no_executor_falls_back_to_direct_start(temp_git_repo: Path) -> None:
    """Without tmux the run still works, and ran_in_tmux reports the truth."""

    write_config(temp_git_repo, Config(tmux=TmuxSettings(enabled=False)))
    workflow = _approved(temp_git_repo)

    runner = WorkflowRunner(temp_git_repo, adapter=ApiAdapter())  # type: ignore[arg-type]
    assert runner._executor is None
    report = runner.run(workflow)
    assert not any(plan.ran_in_tmux for plan in report.stages)


def test_timeout_blocks_without_reverting_the_worktree(temp_git_repo: Path) -> None:
    """A timed-out harness may still be writing: never reset --hard under it."""

    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    executor = RecordingExecutor(exit_code=None, timed_out=True, writes_file="partial.txt")

    runner = WorkflowRunner(
        temp_git_repo,
        adapter=ProcessAdapter(),  # type: ignore[arg-type]
        executor=executor,
    )
    with pytest.raises(StageBlockedError, match="timed out"):
        runner.run(workflow)

    events = EventLog(temp_git_repo).read()
    timeouts = [e for e in events if e.type == "stage.timeout"]
    assert len(timeouts) == 1
    assert timeouts[0].attributes["pane_id"] == "%3"
    # The partial work is still there for inspection.
    assert (worktree_path(temp_git_repo, "analyse") / "partial.txt").exists()
    assert workflow.stages[0].status is StageStatus.FAILED


def test_nonzero_exit_records_code_and_output_tail(temp_git_repo: Path) -> None:
    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    executor = RecordingExecutor(exit_code=2)

    runner = WorkflowRunner(
        temp_git_repo,
        adapter=ProcessAdapter(),  # type: ignore[arg-type]
        executor=executor,
    )
    with pytest.raises(StageBlockedError):
        runner.run(workflow)

    failed = [e for e in EventLog(temp_git_repo).read() if e.type == "stage.failed"]
    assert failed[0].attributes["exit_code"] == 2
    assert failed[0].attributes["output_tail"] == ["harness output"]


def test_unavailable_adapter_reports_that_rather_than_did_not_start(temp_git_repo: Path) -> None:
    class MissingAdapter(ProcessAdapter):
        name = "missing"

        def available(self) -> bool:
            return False

    init_config(temp_git_repo)
    workflow = _approved(temp_git_repo)
    runner = WorkflowRunner(
        temp_git_repo,
        adapter=MissingAdapter(),  # type: ignore[arg-type]
        executor=RecordingExecutor(),
    )
    with pytest.raises(StageBlockedError, match="unavailable"):
        runner.run(workflow)

    failed = [e for e in EventLog(temp_git_repo).read() if e.type == "stage.failed"]
    assert "unavailable" in str(failed[0].attributes["reason"])
