"""Profiles picker: two-step resolution, config table, auto-downgrade tiers."""

from __future__ import annotations

import pytest

from agentrail.models import Role, Tier
from agentrail.profiles import (
    DEFAULT_REGISTRY,
    ProfileRegistry,
    Provider,
    TierSpec,
    WorkKind,
    tier_for_work,
)


def test_resolve_returns_typed_profile() -> None:
    profile = DEFAULT_REGISTRY.resolve(Provider.ANTHROPIC, Tier.DEEP)
    assert profile.provider == Provider.ANTHROPIC
    assert profile.tier is Tier.DEEP
    assert profile.model_id == "claude-opus-4-8"
    assert profile.name == "anthropic-deep"


def test_verified_model_ids_from_spec() -> None:
    assert DEFAULT_REGISTRY.resolve(Provider.ANTHROPIC, Tier.FAST).model_id == (
        "claude-haiku-4-5-20251001"
    )
    gemini_deep = DEFAULT_REGISTRY.resolve(Provider.GOOGLE, Tier.DEEP)
    assert gemini_deep.model_id == "gemini-3.1-pro-preview"
    assert gemini_deep.thinking_param == "HIGH"
    assert DEFAULT_REGISTRY.resolve(Provider.OPENAI, Tier.BALANCED).model_id == "gpt-5.6-terra"


def test_every_provider_has_all_three_tiers() -> None:
    for provider in DEFAULT_REGISTRY.providers():
        tiers = set(DEFAULT_REGISTRY.tiers(provider))
        assert tiers == {Tier.DEEP, Tier.BALANCED, Tier.FAST}


def test_unknown_provider_and_tier_raise() -> None:
    tiny = ProfileRegistry(table={Provider.OPENAI: {Tier.FAST: TierSpec(model_id="x")}})
    with pytest.raises(KeyError):
        tiny.resolve(Provider.GOOGLE, Tier.FAST)
    with pytest.raises(KeyError):
        tiny.resolve(Provider.OPENAI, Tier.DEEP)


def test_auto_downgrade_work_tier_mapping() -> None:
    assert tier_for_work(WorkKind.PLANNING) is Tier.DEEP
    assert tier_for_work(WorkKind.ARCHITECTURE) is Tier.DEEP
    assert tier_for_work(WorkKind.IMPLEMENTATION) is Tier.BALANCED
    assert tier_for_work(WorkKind.LINT) is Tier.FAST
    assert tier_for_work(WorkKind.FORMAT) is Tier.FAST


def test_registry_round_trips_through_config_dict() -> None:
    dumped = DEFAULT_REGISTRY.model_dump(mode="json")
    restored = ProfileRegistry.model_validate(dumped)
    assert restored.resolve(Provider.GOOGLE, Tier.FAST).thinking_param == "MINIMAL"


def test_zai_verified_ids() -> None:
    assert DEFAULT_REGISTRY.resolve(Provider.ZAI, Tier.DEEP).model_id == "glm-5.3"
    assert DEFAULT_REGISTRY.resolve(Provider.ZAI, Tier.BALANCED).model_id == "glm-5.2"
    assert DEFAULT_REGISTRY.resolve(Provider.ZAI, Tier.FAST).model_id == "glm-5.3-flash"


def test_opencode_verified_ids() -> None:
    assert DEFAULT_REGISTRY.resolve(Provider.OPENCODE, Tier.DEEP).model_id == "kimi-k3"
    assert DEFAULT_REGISTRY.resolve(Provider.OPENCODE, Tier.BALANCED).model_id == "qwen3.8-max"
    assert DEFAULT_REGISTRY.resolve(Provider.OPENCODE, Tier.FAST).model_id == "qwen3.8-flash"


def test_role_enum_covers_pipeline() -> None:
    assert {r.value for r in Role} == {"plan", "research", "code", "review"}
