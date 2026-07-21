"""AgentRail profiles subsystem — two-step model/provider/reasoning picker."""

from __future__ import annotations

from agentrail.profiles.registry import (
    DEFAULT_REGISTRY,
    ProfileRegistry,
    Provider,
    TierSpec,
    WorkKind,
    tier_for_work,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "ProfileRegistry",
    "Provider",
    "TierSpec",
    "WorkKind",
    "tier_for_work",
]
