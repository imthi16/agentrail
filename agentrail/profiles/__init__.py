"""AgentRail profiles subsystem — two-step model/provider/reasoning picker."""

from __future__ import annotations

from agentrail.profiles.budget import (
    DEFAULT_PRICES,
    BudgetExceededError,
    BudgetTracker,
    estimate_cost,
)
from agentrail.profiles.registry import (
    DEFAULT_REGISTRY,
    ProfileRegistry,
    Provider,
    Role,
    TierSpec,
    WorkKind,
    tier_for_work,
)
from agentrail.profiles.router import ModelRouter, RoleBindSpec

__all__ = [
    "DEFAULT_PRICES",
    "DEFAULT_REGISTRY",
    "BudgetExceededError",
    "BudgetTracker",
    "ModelRouter",
    "ProfileRegistry",
    "Provider",
    "Role",
    "RoleBindSpec",
    "TierSpec",
    "WorkKind",
    "estimate_cost",
    "tier_for_work",
]
