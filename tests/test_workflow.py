"""DAG validation: cycle rejection, unknown deps, and topological ordering."""

from __future__ import annotations

import pytest

from agentrail.models import Stage
from agentrail.workflow import (
    CycleError,
    WorkflowValidationError,
    topological_order,
    validate_dag,
)


def _stage(sid: str, deps: list[str] | None = None) -> Stage:
    return Stage(id=sid, title=sid.title(), depends_on=deps or [])


def test_topological_order_respects_dependencies() -> None:
    stages = [
        _stage("logout", ["analyse"]),
        _stage("analyse"),
        _stage("login", ["analyse"]),
    ]
    ordered = [s.id for s in topological_order(stages)]

    assert ordered[0] == "analyse"
    assert ordered.index("analyse") < ordered.index("login")
    assert ordered.index("analyse") < ordered.index("logout")


def test_topological_order_is_deterministic() -> None:
    stages = [_stage("b"), _stage("a"), _stage("c", ["a", "b"])]
    first = [s.id for s in topological_order(stages)]
    second = [s.id for s in topological_order(list(reversed(stages)))]

    assert first == second == ["a", "b", "c"]


def test_cycle_is_rejected() -> None:
    stages = [_stage("a", ["b"]), _stage("b", ["a"])]
    with pytest.raises(CycleError):
        topological_order(stages)
    with pytest.raises(CycleError):
        validate_dag(stages)


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(ValueError):
        _stage("a", ["a"])


def test_unknown_dependency_is_rejected() -> None:
    stages = [_stage("a", ["ghost"])]
    with pytest.raises(WorkflowValidationError):
        validate_dag(stages)


def test_duplicate_ids_are_rejected() -> None:
    stages = [_stage("a"), _stage("a")]
    with pytest.raises(WorkflowValidationError):
        validate_dag(stages)
