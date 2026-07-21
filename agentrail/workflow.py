"""DAG Planner + persistence to ``.agentrail/workflow.yaml``.

Pure planning/validation logic: turn a natural-language goal into a
schema-validated stage DAG, reject cycles, topologically sort, and persist in
``awaiting_approval`` status. No git/tmux/network side effects live here so the
planner stays unit-testable; those are injected by subsystems later.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agentrail.config import config_dir
from agentrail.models import Stage, Workflow, WorkflowStatus

WORKFLOW_FILENAME = "workflow.yaml"


class WorkflowValidationError(ValueError):
    """Raised when a stage DAG is structurally invalid."""


class CycleError(WorkflowValidationError):
    """Raised when the stage graph contains a dependency cycle."""


def workflow_path(root: Path) -> Path:
    return config_dir(root) / WORKFLOW_FILENAME


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "stage"


def validate_dag(stages: list[Stage]) -> None:
    """Validate stage ids are unique and dependencies resolve to real stages.

    Raises ``WorkflowValidationError`` for duplicate ids or unknown deps, and
    ``CycleError`` if a cycle exists. Does not mutate the input.
    """

    ids = [stage.id for stage in stages]
    duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
    if duplicates:
        raise WorkflowValidationError(f"duplicate stage ids: {', '.join(duplicates)}")

    id_set = set(ids)
    for stage in stages:
        unknown = [dep for dep in stage.depends_on if dep not in id_set]
        if unknown:
            raise WorkflowValidationError(
                f"stage {stage.id!r} depends on unknown stage(s): {', '.join(unknown)}"
            )

    # Cycle detection is implicit in topological_order; run it to surface errors.
    topological_order(stages)


def topological_order(stages: list[Stage]) -> list[Stage]:
    """Return stages in a deterministic topological order (Kahn's algorithm).

    Ties are broken by stage id so the ordering is stable across runs. Raises
    ``CycleError`` if the graph cannot be fully ordered.
    """

    by_id = {stage.id: stage for stage in stages}
    indegree = {stage.id: 0 for stage in stages}
    dependents: dict[str, list[str]] = {stage.id: [] for stage in stages}

    for stage in stages:
        for dep in stage.depends_on:
            if dep not in by_id:
                raise WorkflowValidationError(
                    f"stage {stage.id!r} depends on unknown stage {dep!r}"
                )
            indegree[stage.id] += 1
            dependents[dep].append(stage.id)

    ready = sorted(sid for sid, deg in indegree.items() if deg == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for child in dependents[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                # keep the ready frontier sorted for determinism
                ready.append(child)
                ready.sort()

    if len(ordered) != len(stages):
        remaining = sorted(set(by_id) - set(ordered))
        raise CycleError(f"dependency cycle involving: {', '.join(remaining)}")

    return [by_id[sid] for sid in ordered]


def _default_stages(goal: str) -> list[Stage]:
    """Heuristic decomposition for v0.1.

    Always produce an ``analyse`` root; if the goal names multiple deliverables
    joined by "and", fan them out as siblings depending on ``analyse`` (the
    canonical login/logout example). Otherwise emit a single implementation
    stage. Real LLM-backed planning arrives with the adapters in a later stage.
    """

    analyse = Stage(
        id="analyse",
        title="Analyse repository and requirements",
        description=f"Understand the codebase and scope the goal: {goal}",
        depends_on=[],
        acceptance_criteria=["Requirements and affected areas are documented"],
    )

    deliverables = _split_deliverables(goal)
    if len(deliverables) >= 2:
        stages = [analyse]
        seen: set[str] = {analyse.id}
        for name in deliverables:
            sid = _unique_id(_slugify(name), seen)
            seen.add(sid)
            stages.append(
                Stage(
                    id=sid,
                    title=f"Implement and validate {name}",
                    description=f"Implement '{name}' toward the goal: {goal}",
                    depends_on=[analyse.id],
                    acceptance_criteria=[f"'{name}' works and is covered by tests"],
                )
            )
        return stages

    implement = Stage(
        id="implement",
        title="Implement the goal",
        description=goal,
        depends_on=[analyse.id],
        acceptance_criteria=["Goal implemented and validated by tests"],
    )
    return [analyse, implement]


def _split_deliverables(goal: str) -> list[str]:
    """Extract deliverable nouns from a goal like 'login and logout as ...'."""

    core = re.split(r"\bas\b", goal, maxsplit=1, flags=re.IGNORECASE)[0]
    core = re.sub(r"^\s*implement\s+", "", core, flags=re.IGNORECASE)
    parts = re.split(r"\band\b|,", core, flags=re.IGNORECASE)
    names = [p.strip() for p in parts if p.strip()]
    return names if len(names) >= 2 else []


def _unique_id(base: str, seen: set[str]) -> str:
    if base not in seen:
        return base
    i = 2
    while f"{base}-{i}" in seen:
        i += 1
    return f"{base}-{i}"


def plan_workflow(goal: str, stages: list[Stage] | None = None) -> Workflow:
    """Build a validated workflow in ``awaiting_approval`` status.

    ``stages`` may be provided explicitly (e.g. from a richer planner); if
    omitted a deterministic heuristic decomposition is used. Validation rejects
    cycles/unknown deps before the workflow is returned.
    """

    goal = goal.strip()
    if not goal:
        raise WorkflowValidationError("goal must not be empty")

    resolved = stages if stages is not None else _default_stages(goal)
    validate_dag(resolved)
    ordered = topological_order(resolved)
    return Workflow(
        goal=goal,
        status=WorkflowStatus.AWAITING_APPROVAL,
        stages=ordered,
    )


def save_workflow(root: Path, workflow: Workflow) -> Path:
    """Serialize the workflow through the model to ``.agentrail/workflow.yaml``."""

    validate_dag(workflow.stages)
    path = workflow_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = workflow.model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def load_workflow(root: Path) -> Workflow:
    """Read and validate the persisted workflow."""

    path = workflow_path(root)
    if not path.exists():
        raise FileNotFoundError(f"no workflow at {path} (run `agentrail plan`)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"workflow at {path} must be a mapping")
    workflow = Workflow.model_validate(data)
    validate_dag(workflow.stages)
    return workflow
