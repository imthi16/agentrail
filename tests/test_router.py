"""Model router: tier resolution + tier-change event logging."""

from __future__ import annotations

from pathlib import Path

from agentrail.events import EventLog
from agentrail.models import Tier
from agentrail.profiles import ModelRouter, Provider, WorkKind


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
