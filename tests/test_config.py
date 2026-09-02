"""Config load/write round-trip incl. budgets, and validation failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentrail.config import (
    Budget,
    Config,
    RoleBind,
    init_config,
    load_config,
    write_config,
)
from agentrail.models import Mode


def test_default_config_has_zero_budget_limits() -> None:
    cfg = Config()
    assert cfg.default_mode is Mode.PLAN
    assert cfg.budget.max_usd is None
    assert cfg.budget.max_tokens is None
    assert cfg.budget.max_retries_per_stage == 2


def test_budget_round_trips_through_yaml(project_root: Path) -> None:
    cfg = Config(budget=Budget(max_usd=25.0, max_tokens=1_000_000, max_retries_per_stage=3))
    write_config(project_root, cfg)

    reloaded = load_config(project_root)
    assert reloaded.budget.max_usd == 25.0
    assert reloaded.budget.max_tokens == 1_000_000
    assert reloaded.budget.max_retries_per_stage == 3


def test_init_creates_then_finds(project_root: Path) -> None:
    cfg1, created1 = init_config(project_root)
    assert created1 is True
    _cfg2, created2 = init_config(project_root)
    assert created2 is False


def test_unknown_field_is_rejected(project_root: Path) -> None:
    path = project_root / ".agentrail" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"bogus": 1}), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(project_root)


def test_negative_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Budget(max_usd=-1.0)


def test_roles_default_to_mixed_pipeline() -> None:
    from agentrail.models import Provider, Role, Tier

    cfg = Config()
    assert cfg.roles[Role.PLAN] == RoleBind(provider=Provider.ANTHROPIC, tier=Tier.DEEP)
    assert cfg.roles[Role.CODE] == RoleBind(provider=Provider.ZAI, tier=Tier.DEEP)
    assert cfg.roles[Role.REVIEW] == RoleBind(provider=Provider.OPENAI, tier=Tier.DEEP)
    assert cfg.roles[Role.RESEARCH] == RoleBind(provider=Provider.OPENCODE, tier=Tier.DEEP)


def test_opencode_defaults_to_go_endpoint() -> None:
    cfg = Config()
    assert cfg.opencode.base_url == "https://opencode.ai/go/v1"
    assert cfg.opencode.api_key_env == "OPENCODE_API_KEY"


def test_roles_round_trip_through_yaml(project_root: Path) -> None:
    from agentrail.models import Provider, Role, Tier

    cfg = Config(roles={Role.CODE: RoleBind(provider=Provider.OPENCODE, tier=Tier.BALANCED)})
    write_config(project_root, cfg)
    reloaded = load_config(project_root)
    assert reloaded.roles[Role.CODE].provider is Provider.OPENCODE
    assert reloaded.roles[Role.CODE].tier is Tier.BALANCED


def test_bad_role_provider_string_raises(project_root: Path) -> None:
    path = project_root / ".agentrail" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"roles": {"code": {"provider": "not-a-provider", "tier": "deep"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(project_root)
