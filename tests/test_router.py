"""Model router: tier resolution + tier-change event logging."""

from __future__ import annotations

from pathlib import Path

from agentrail.events import EventLog
from agentrail.models import Role, Tier
from agentrail.profiles import ModelRouter, Provider, RoleBindSpec, WorkKind


def test_route_resolves_and_logs_first_selection(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    router = ModelRouter(provider=Provider.ANTHROPIC, event_log=log, workflow_id="wf-1")

    profile = router.route(WorkKind.PLANNING, stage_id="analyse")
    assert profile.tier is Tier.DEEP
    assert profile.model_id == "claude-opus-4-8"

    events = [e for e in log.read() if e.type == "profile.tier_changed"]
    assert len(events) == 1
    assert events[0].attributes["tier"] == "deep"
    assert events[0].attributes["provider"] == "anthropic"


def test_downgrade_logs_only_on_change(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    router = ModelRouter(provider=Provider.OPENAI, event_log=log, workflow_id="wf-1")

    router.route(WorkKind.PLANNING)  # deep
    router.route(WorkKind.ARCHITECTURE)  # still deep -> no new event
    router.route(WorkKind.IMPLEMENTATION)  # balanced -> logged
    router.route(WorkKind.LINT)  # fast -> logged

    changes = [e for e in log.read() if e.type == "profile.tier_changed"]
    tiers = [e.attributes["tier"] for e in changes]
    assert tiers == ["deep", "balanced", "fast"]
    assert changes[-1].attributes["previous_tier"] == "balanced"


def test_route_role_resolves_bind_and_logs_role(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    router = ModelRouter(
        provider=Provider.ANTHROPIC,
        event_log=log,
        workflow_id="wf-1",
        role_binds={
            Role.CODE: RoleBindSpec(provider=Provider.ZAI, tier=Tier.DEEP),
        },
    )

    profile = router.route_role(Role.CODE, stage_id="implement")
    assert profile.provider == Provider.ZAI
    assert profile.tier is Tier.DEEP
    assert profile.model_id == "glm-5.3"

    events = [e for e in log.read() if e.type == "profile.tier_changed"]
    assert len(events) == 1
    assert events[0].attributes["role"] == "code"
    assert events[0].attributes["provider"] == "zai"


def test_route_role_falls_back_to_work_kind_when_unbound(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    router = ModelRouter(provider=Provider.ANTHROPIC, event_log=log, workflow_id="wf-1")

    profile = router.route_role(Role.PLAN, stage_id="analyse")
    # No bind: falls back to route(WorkKind.IMPLEMENTATION) on the default provider.
    assert profile.provider == Provider.ANTHROPIC
    assert profile.tier is Tier.BALANCED


def test_route_role_change_only_logs_once_per_role(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    router = ModelRouter(
        provider=Provider.ANTHROPIC,
        event_log=log,
        workflow_id="wf-1",
        role_binds={Role.REVIEW: RoleBindSpec(provider=Provider.OPENAI, tier=Tier.DEEP)},
    )

    router.route_role(Role.REVIEW)
    router.route_role(Role.REVIEW)  # unchanged -> no second event

    changes = [e for e in log.read() if e.type == "profile.tier_changed"]
    assert len(changes) == 1
    assert changes[0].attributes["provider"] == "openai"
