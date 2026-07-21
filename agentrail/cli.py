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
from agentrail.config import config_path, init_config, load_config
from agentrail.events import EventLog, new_id
from agentrail.models import Stage
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
        load_config(root)
    except FileNotFoundError:
        console.print("[red]No AgentRail project here.[/red] Run `agentrail init` first.")
        raise typer.Exit(code=1) from None

    try:
        workflow = plan_workflow(goal)
    except WorkflowValidationError as exc:
        console.print(f"[red]Planning failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

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
        console.print("[yellow]No workflow yet.[/yellow] Run `agentrail plan \"<goal>\"`.")
        raise typer.Exit(code=1) from None

    console.print(f"[bold]Goal:[/bold] {workflow.goal}")
    console.print(f"[bold]Status:[/bold] {workflow.status.value}   mode: {workflow.mode.value}")
    console.print(f"[dim]{workflow_path(root)}[/dim]")
    _render_stages(workflow.stages)


def _render_stages(stages: list[Stage]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("title")
    table.add_column("depends_on")
    table.add_column("status")
    for stage in stages:
        table.add_row(
            stage.id,
            stage.title,
            ", ".join(stage.depends_on) or "-",
            stage.status.value,
        )
    console.print(table)


def main() -> None:
    """Entry point declared in pyproject.toml (``agentrail.cli:main``)."""

    app()


if __name__ == "__main__":
    main()
