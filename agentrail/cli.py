"""AgentRail CLI (Typer). Commands orchestrate; subsystem logic lives elsewhere.

v0.1 exposes ``version``, ``init``, ``plan``, and ``status``. Later stages add
``run``, ``pause``, ``resume``, and ``rollback``.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agentrail import __version__
from agentrail.checkpoints import CheckpointError, CheckpointStore
from agentrail.config import config_path, init_config, load_config
from agentrail.events import EventLog, new_id
from agentrail.git import PullRequestError, PullRequestManager
from agentrail.models import Stage, Workflow, WorkflowStatus
from agentrail.runner import StageBlockedError, WorkflowRunner
from agentrail.workflow import (
    WorkflowValidationError,
    load_workflow,
    plan_workflow,
    save_workflow,
    workflow_path,
)

app = typer.Typer(
    name="agentrail",
    help="Safe, stage-based workflow control plane for AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _root() -> Path:
    return Path.cwd()


@app.command()
def version() -> None:
    """Print the AgentRail version."""

    console.print(f"agentrail {__version__}")


@app.command()
def init() -> None:
    """Initialize ``.agentrail/`` with a default config in the current project."""

    root = _root()
    config, created = init_config(root)
    EventLog(root).emit(
        type="project.initialized",
        workflow_id="-",
        trace_id=new_id(),
        mode=config.default_mode,
        attributes={"created": created, "harness": config.harness},
    )
    action = "Created" if created else "Found existing"
    console.print(f"[green]{action}[/green] {config_path(root)}")
    console.print(
        f"  default_mode={config.default_mode.value} "
        f"default_profile={config.default_profile} harness={config.harness}"
    )


@app.command()
def plan(goal: str = typer.Argument(..., help="Natural-language goal to plan.")) -> None:
    """Decompose a goal into a stage DAG and persist it (awaiting approval)."""

    root = _root()
    try:
        config = load_config(root)
    except FileNotFoundError:
        console.print("[red]No AgentRail project here.[/red] Run `agentrail init` first.")
        raise typer.Exit(code=1) from None

    try:
        workflow = plan_workflow(goal)
    except WorkflowValidationError as exc:
        console.print(f"[red]Planning failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Seed the workflow's operating mode from the project config so `run`
    # enforces the configured mode (e.g. auto) rather than always defaulting.
    workflow.mode = config.default_mode
    path = save_workflow(root, workflow)
    EventLog(root).emit(
        type="workflow.planned",
        workflow_id=workflow.workflow_id,
        trace_id=new_id(),
        mode=workflow.mode,
        attributes={
            "goal": workflow.goal,
            "status": workflow.status.value,
            "stage_ids": [s.id for s in workflow.stages],
        },
    )
    console.print(f"[green]Planned[/green] {len(workflow.stages)} stage(s) → {path}")
    console.print(f"  status: [yellow]{workflow.status.value}[/yellow]")
    _render_stages(workflow.stages)


@app.command()
def status() -> None:
    """Show the current workflow status and its stage DAG."""

    root = _root()
    try:
        workflow = load_workflow(root)
    except FileNotFoundError:
        console.print('[yellow]No workflow yet.[/yellow] Run `agentrail plan "<goal>"`.')
        raise typer.Exit(code=1) from None

    console.print(f"[bold]Goal:[/bold] {workflow.goal}")
    console.print(f"[bold]Status:[/bold] {workflow.status.value}   mode: {workflow.mode.value}")
    console.print(f"[dim]{workflow_path(root)}[/dim]")
    _render_stages(workflow.stages)


@app.command()
def rollback(
    stage_id: str = typer.Argument(..., help="Stage id to roll back."),
    checkpoint_id: str = typer.Option(
        "", "--checkpoint", "-c", help="Checkpoint id (default: latest)."
    ),
) -> None:
    """Roll a stage's worktree back to a checkpoint (that worktree only)."""

    root = _root()
    try:
        workflow = load_workflow(root)
    except FileNotFoundError:
        console.print('[yellow]No workflow yet.[/yellow] Run `agentrail plan "<goal>"`.')
        raise typer.Exit(code=1) from None

    stage = next((s for s in workflow.stages if s.id == stage_id), None)
    if stage is None:
        console.print(f"[red]Unknown stage:[/red] {stage_id}")
        raise typer.Exit(code=1)
    if stage.worktree is None:
        console.print(f"[red]Stage {stage_id} has no worktree to roll back.[/red]")
        raise typer.Exit(code=1)

    store = CheckpointStore(root)
    available = store.list(stage_id)
    target = checkpoint_id or (available[-1] if available else "")
    if not target:
        console.print(f"[red]No checkpoints recorded for stage {stage_id}.[/red]")
        raise typer.Exit(code=1)

    try:
        head = store.rollback(stage_id, Path(stage.worktree.path), target)
    except CheckpointError as exc:
        console.print(f"[red]Rollback failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    EventLog(root).emit(
        type="stage.rolled_back",
        workflow_id=workflow.workflow_id,
        trace_id=new_id(),
        stage_id=stage_id,
        mode=workflow.mode,
        attributes={"checkpoint_id": target, "git_head": head},
    )
    console.print(f"[green]Rolled back[/green] {stage_id} → {target} ({head[:12]})")


@app.command()
def ready(
    stage_id: str = typer.Argument("", help="Stage id to promote (default: all stages with a PR)."),
    checks_passed: bool = typer.Option(
        False,
        "--checks-passed/--checks-failed",
        help="Assert the quality gate (CI/tests) passed for the stage(s).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not call gh; report only."),
) -> None:
    """Promote stage draft PR(s) to ready when the merge gate passes.

    Merge gate = acceptance criteria present AND --checks-passed. Fails closed.
    """

    root = _root()
    workflow = _load_or_exit(root)
    manager = PullRequestManager(root)

    targets = [
        s
        for s in workflow.stages
        if s.pull_request is not None and (not stage_id or s.id == stage_id)
    ]
    if stage_id and not any(s.id == stage_id for s in workflow.stages):
        console.print(f"[red]Unknown stage:[/red] {stage_id}")
        raise typer.Exit(code=1)
    if not targets:
        console.print("[yellow]No stages with a PR to promote.[/yellow] Run `agentrail run`.")
        raise typer.Exit(code=1)

    promoted = 0
    for stage in targets:
        assert stage.pull_request is not None
        try:
            updated = manager.mark_ready(
                stage.pull_request, stage, checks_passed=checks_passed, dry_run=dry_run
            )
        except PullRequestError as exc:
            console.print(f"[red]{stage.id}: merge gate blocked[/red] — {exc}")
            continue
        stage.pull_request = updated
        state = "ready" if not updated.draft else "draft (degraded/dry-run)"
        console.print(f"[green]{stage.id}[/green] → {state}")
        EventLog(root).emit(
            type="pr.ready" if not updated.draft else "pr.ready_skipped",
            workflow_id=workflow.workflow_id,
            trace_id=new_id(),
            stage_id=stage.id,
            mode=workflow.mode,
            attributes={"checks_passed": checks_passed, "dry_run": dry_run},
        )
        if not updated.draft:
            promoted += 1

    save_workflow(root, workflow)
    console.print(f"Promoted {promoted}/{len(targets)} PR(s) to ready.")


def _render_stages(stages: list[Stage]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("title")
    table.add_column("depends_on")
    table.add_column("status")
    table.add_column("PR")
    for stage in stages:
        pr = "-"
        if stage.pull_request is not None:
            state = "draft" if stage.pull_request.draft else "ready"
            num = stage.pull_request.number
            pr = f"#{num} {state}" if num is not None else state
        table.add_row(
            stage.id,
            stage.title,
            ", ".join(stage.depends_on) or "-",
            stage.status.value,
            pr,
        )
    console.print(table)


@app.command()
def approve() -> None:
    """Approve the current workflow so it can be run."""

    root = _root()
    workflow = _load_or_exit(root)
    if workflow.status not in (WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.PAUSED):
        console.print(f"[yellow]Nothing to approve (status={workflow.status.value}).[/yellow]")
        raise typer.Exit(code=0)
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(root, workflow)
    EventLog(root).emit(
        type="workflow.approved",
        workflow_id=workflow.workflow_id,
        trace_id=new_id(),
        mode=workflow.mode,
    )
    console.print("[green]Approved.[/green] Run with `agentrail run` (or --dry-run).")


@app.command()
def run(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan the full stage sequence without side effects."
    ),
) -> None:
    """Execute the approved workflow stage-by-stage (worktree, checkpoint, PR)."""

    root = _root()
    workflow = _load_or_exit(root)
    if dry_run and workflow.status == WorkflowStatus.AWAITING_APPROVAL:
        workflow.status = WorkflowStatus.APPROVED  # dry-run needs no real approval
    if workflow.status not in (WorkflowStatus.APPROVED, WorkflowStatus.RUNNING):
        console.print(
            f"[red]Workflow not approved[/red] (status={workflow.status.value}). "
            "Run `agentrail approve` first."
        )
        raise typer.Exit(code=1)

    runner = WorkflowRunner(root, mode=workflow.mode)
    try:
        report = runner.run(workflow, dry_run=dry_run)
    except StageBlockedError as exc:
        console.print(f"[red]Blocked by policy:[/red] {exc}")
        console.print(
            "[yellow]Fix the Intent Lock / mode, then re-run.[/yellow] "
            "State was saved (status: paused)."
        )
        raise typer.Exit(code=2) from exc

    label = "DRY-RUN plan" if dry_run else "Executed"
    console.print(f"[green]{label}[/green] for {len(report.stages)} stage(s):")
    table = Table(show_header=True, header_style="bold")
    table.add_column("stage")
    table.add_column("worktree branch")
    table.add_column("base")
    table.add_column("PR base -> head")
    table.add_column("tmux")
    for plan in report.stages:
        table.add_row(
            plan.stage_id,
            plan.worktree_branch,
            plan.worktree_base,
            f"{plan.pr_base} -> {plan.pr_head}",
            "yes" if plan.ran_in_tmux else "no",
        )
    console.print(table)


@app.command()
def pause() -> None:
    """Pause a running workflow."""

    root = _root()
    workflow = _load_or_exit(root)
    workflow.status = WorkflowStatus.PAUSED
    save_workflow(root, workflow)
    EventLog(root).emit(
        type="workflow.paused",
        workflow_id=workflow.workflow_id,
        trace_id=new_id(),
        mode=workflow.mode,
    )
    console.print("[yellow]Paused.[/yellow] Resume with `agentrail resume`.")


@app.command()
def resume() -> None:
    """Resume a paused workflow (marks it approved so it can run again)."""

    root = _root()
    workflow = _load_or_exit(root)
    if workflow.status is not WorkflowStatus.PAUSED:
        console.print(f"[yellow]Not paused (status={workflow.status.value}).[/yellow]")
        raise typer.Exit(code=0)
    workflow.status = WorkflowStatus.APPROVED
    save_workflow(root, workflow)
    EventLog(root).emit(
        type="workflow.resumed",
        workflow_id=workflow.workflow_id,
        trace_id=new_id(),
        mode=workflow.mode,
    )
    console.print("[green]Resumed.[/green] Continue with `agentrail run`.")


def _load_or_exit(root: Path) -> Workflow:
    try:
        return load_workflow(root)
    except FileNotFoundError:
        console.print('[yellow]No workflow yet.[/yellow] Run `agentrail plan "<goal>"`.')
        raise typer.Exit(code=1) from None


def main() -> None:
    """Entry point declared in pyproject.toml (``agentrail.cli:main``)."""

    app()


if __name__ == "__main__":
    main()
