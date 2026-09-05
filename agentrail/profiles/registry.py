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

from agentrail.models import Profile, Provider, Role, Tier

__all__ = [
    "DEFAULT_REGISTRY",
    "ProfileRegistry",
    "Provider",  # re-export: canonical definition lives in models.py
    "Role",  # re-export likewise
    "TierSpec",
    "WorkKind",
    "tier_for_work",
]


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
        Provider.ZAI: {
            # z.ai (docs.z.ai, verified Aug 2026): GLM-5.3 is the open-weights
            # #1 coding model; GLM-5.3-Flash is radically cheaper ($0.07/$0.25).
            # thinking is mandatory on 5.3; effort levels Low/High/Max.
            Tier.DEEP: TierSpec(model_id="glm-5.3", thinking_param="max"),
            Tier.BALANCED: TierSpec(model_id="glm-5.2", thinking_param="high"),
            Tier.FAST: TierSpec(model_id="glm-5.3-flash", thinking_param="low"),
        },
        Provider.OPENCODE: {
            # OpenCode Go/Zen catalog (opencode.ai/docs/zen, verified Aug 2026).
            # Kimi K3 = repo-scale coding; Qwen3.8 Max = balanced; Qwen3.8 Flash
            # = cheap/fast. Deprecated IDs (kimi-k2.x, glm-5.1) never pinned.
            Tier.DEEP: TierSpec(model_id="kimi-k3"),
            Tier.BALANCED: TierSpec(model_id="qwen3.8-max"),
            Tier.FAST: TierSpec(model_id="qwen3.8-flash"),
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
