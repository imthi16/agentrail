"""Model / provider / reasoning picker (two-step: provider -> effort tier).

The picker is TWO steps: (1) choose family/provider, (2) choose effort tier
Deep / Balanced / Fast. Raw model strings are NEVER exposed in code paths;
everything routes through a config table so volatile model IDs live in one place.

The bundled ``DEFAULT_REGISTRY`` uses the verified IDs from
``agentrail/profiles/CLAUDE.md`` (as of July 2026). Projects can override the
table via ``.agentrail/config.yaml`` so pins are re-verifiable without code edits.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agentrail.models import Profile, Tier


class Provider(StrEnum):
    """Model family / provider (step one of the picker)."""

    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI = "openai"


class TierSpec(BaseModel):
    """A concrete (model_id, thinking_param) pin for one (provider, tier)."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    thinking_param: str | None = None


class ProfileRegistry(BaseModel):
    """Config table: ``{provider: {tier: TierSpec}}``.

    The single source of truth for model routing. Code references
    ``(provider, tier)`` only; it never hardcodes a model string.
    """

    model_config = ConfigDict(extra="forbid")

    table: dict[Provider, dict[Tier, TierSpec]] = Field(default_factory=dict)

    def resolve(self, provider: Provider, tier: Tier) -> Profile:
        """Resolve ``(provider, tier)`` to a fully-typed :class:`Profile`."""

        by_tier = self.table.get(provider)
        if by_tier is None:
            raise KeyError(f"unknown provider: {provider}")
        spec = by_tier.get(tier)
        if spec is None:
            raise KeyError(f"provider {provider} has no {tier} tier")
        return Profile(
            name=f"{provider}-{tier}",
            provider=provider,
            tier=tier,
            model_id=spec.model_id,
            thinking_param=spec.thinking_param,
        )

    def providers(self) -> list[Provider]:
        return list(self.table.keys())

    def tiers(self, provider: Provider) -> list[Tier]:
        by_tier = self.table.get(provider)
        if by_tier is None:
            raise KeyError(f"unknown provider: {provider}")
        return list(by_tier.keys())


# --- Verified defaults (agentrail/profiles/CLAUDE.md) -----------------------
# NOTE: "Claude 3.5 Opus" does NOT exist. Re-verify against live model lists
# before pinning in production; H1 2026 rotated these repeatedly.
DEFAULT_REGISTRY = ProfileRegistry(
    table={
        Provider.ANTHROPIC: {
            # Claude reasoning depth is the extended-thinking budget / Effort
            # selector; there is no `reasoning_effort` field.
            Tier.DEEP: TierSpec(model_id="claude-opus-4-8"),
            Tier.BALANCED: TierSpec(model_id="claude-sonnet-4-6"),
            Tier.FAST: TierSpec(model_id="claude-haiku-4-5-20251001"),
        },
        Provider.GOOGLE: {
            # thinking_level (MINIMAL/LOW/MEDIUM/HIGH) REPLACES thinking_budget.
            Tier.DEEP: TierSpec(model_id="gemini-3.1-pro-preview", thinking_param="HIGH"),
            Tier.BALANCED: TierSpec(model_id="gemini-3.5-flash", thinking_param="MEDIUM"),
            Tier.FAST: TierSpec(model_id="gemini-3.1-flash-lite", thinking_param="MINIMAL"),
        },
        Provider.OPENAI: {
            # reasoning_effort for GPT-5.6: none/low/medium/high/xhigh/max.
            Tier.DEEP: TierSpec(model_id="gpt-5.6-sol", thinking_param="high"),
            Tier.BALANCED: TierSpec(model_id="gpt-5.6-terra", thinking_param="medium"),
            Tier.FAST: TierSpec(model_id="gpt-5.6-luna", thinking_param="low"),
        },
    }
)


# --- Auto-downgrade policy --------------------------------------------------
class WorkKind(StrEnum):
    """The kind of work being routed (drives the default tier)."""

    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    LINT = "lint"
    FORMAT = "format"
    TRIVIAL = "trivial"


_WORK_TIER: dict[WorkKind, Tier] = {
    WorkKind.PLANNING: Tier.DEEP,
    WorkKind.ARCHITECTURE: Tier.DEEP,
    WorkKind.IMPLEMENTATION: Tier.BALANCED,
    WorkKind.LINT: Tier.FAST,
    WorkKind.FORMAT: Tier.FAST,
    WorkKind.TRIVIAL: Tier.FAST,
}


def tier_for_work(kind: WorkKind) -> Tier:
    """Map a work kind to its default effort tier (planning->Deep, ...)."""

    return _WORK_TIER[kind]
