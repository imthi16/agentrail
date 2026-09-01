"""Model router — resolve work to a profile and log every tier change to events.

Combines the two-step picker (:mod:`agentrail.profiles.registry`) with the
auto-downgrade policy and the event timeline. Every tier selection or mid-
workflow downgrade is logged with provider + model_id + tier so the decision is
auditable and replayable (profiles/CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentrail.events import EventLog, new_id
from agentrail.models import Profile, Role, Tier
from agentrail.profiles.registry import (
    DEFAULT_REGISTRY,
    ProfileRegistry,
    Provider,
    WorkKind,
    tier_for_work,
)


@dataclass
class RoleBindSpec:
    """Resolver-side (provider, tier) bind for one role."""

    provider: Provider
    tier: Tier


@dataclass
class ModelRouter:
    """Resolves (provider, work-kind) to a profile and logs the choice.

    Optional ``role_binds`` route named roles (plan/research/code/review) to a
    specific (provider, tier); unbound roles fall back to ``route(work_kind)``
    on the router's default provider.
    """

    provider: Provider
    event_log: EventLog
    workflow_id: str
    registry: ProfileRegistry = field(default_factory=lambda: DEFAULT_REGISTRY)
    trace_id: str | None = None
    role_binds: dict[Role, RoleBindSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._trace_id = self.trace_id or new_id()
        self._current: Tier | None = None
        self._by_role: dict[str, Tier | None] = {}

    def route(self, kind: WorkKind, *, stage_id: str | None = None) -> Profile:
        """Pick the tier for ``kind`` and log a tier change if it differs."""

        tier = tier_for_work(kind)
        profile = self.registry.resolve(self.provider, tier)
        if tier != self._current:
            self._log_change(kind.value, tier, profile, stage_id=stage_id)
            self._current = tier
        return profile

    def route_role(self, role: Role, *, stage_id: str | None = None) -> Profile:
        """Resolve a pipeline role through its (provider, tier) bind."""

        bind = self.role_binds.get(role)
        if bind is None:
            return self.route(WorkKind.IMPLEMENTATION, stage_id=stage_id)
        profile = self.registry.resolve(bind.provider, bind.tier)
        if bind.tier != self._by_role.get(role.value):
            self._log_change(
                role.value, bind.tier, profile, stage_id=stage_id, provider=bind.provider
            )
            self._by_role[role.value] = bind.tier
        return profile

    def _log_change(
        self,
        label: str,
        tier: Tier,
        profile: Profile,
        *,
        stage_id: str | None,
        provider: Provider | None = None,
    ) -> None:
        self.event_log.emit(
            type="profile.tier_changed",
            workflow_id=self.workflow_id,
            trace_id=self._trace_id,
            stage_id=stage_id,
            attributes={
                "work_kind": label,
                "role": label,
                "provider": (provider or self.provider).value,
                "tier": tier.value,
                "model_id": profile.model_id,
                "thinking_param": profile.thinking_param,
                "previous_tier": self._current.value if self._current else None,
            },
        )
