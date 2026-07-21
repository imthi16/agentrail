"""Model router — resolve work to a profile and log every tier change to events.

Combines the two-step picker (:mod:`agentrail.profiles.registry`) with the
auto-downgrade policy and the event timeline. Every tier selection or mid-
workflow downgrade is logged with provider + model_id + tier so the decision is
auditable and replayable (profiles/CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentrail.events import EventLog, new_id
from agentrail.models import Profile, Tier
from agentrail.profiles.registry import (
    DEFAULT_REGISTRY,
    ProfileRegistry,
    Provider,
    WorkKind,
    tier_for_work,
)


@dataclass
class ModelRouter:
    """Resolves (provider, work-kind) to a profile and logs the choice."""

    provider: Provider
    event_log: EventLog
    workflow_id: str
    registry: ProfileRegistry = field(default_factory=lambda: DEFAULT_REGISTRY)
    trace_id: str | None = None

    def __post_init__(self) -> None:
        self._trace_id = self.trace_id or new_id()
        self._current: Tier | None = None

    def route(self, kind: WorkKind, *, stage_id: str | None = None) -> Profile:
        """Pick the tier for ``kind`` and log a tier change if it differs."""

        tier = tier_for_work(kind)
        profile = self.registry.resolve(self.provider, tier)
        if tier != self._current:
            self._log_change(kind, profile, stage_id=stage_id)
            self._current = tier
        return profile

    def _log_change(self, kind: WorkKind, profile: Profile, *, stage_id: str | None) -> None:
        self.event_log.emit(
            type="profile.tier_changed",
            workflow_id=self.workflow_id,
            trace_id=self._trace_id,
            stage_id=stage_id,
            attributes={
                "work_kind": kind.value,
                "provider": profile.provider,
                "tier": profile.tier.value,
                "model_id": profile.model_id,
                "thinking_param": profile.thinking_param,
                "previous_tier": self._current.value if self._current else None,
            },
        )
