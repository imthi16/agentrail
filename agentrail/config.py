"""Project configuration — load/write ``.agentrail/config.yaml``.

Holds project-level defaults (mode, profile, harness, budgets). Validated on
load via a pydantic model so malformed config fails loudly rather than silently.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentrail.models import GuardCheck, Mode, Provider, Role, Tier

AGENTRAIL_DIR = ".agentrail"
CONFIG_FILENAME = "config.yaml"


class Budget(BaseModel):
    """Cost and retry ceilings for a workflow (enforced by BudgetTracker).

    ``None`` on a spend limit means "unbounded". ``max_retries_per_stage`` caps
    edit-guard / stage retries. Deny-closed: exceeding any set limit blocks
    further spend.
    """

    model_config = ConfigDict(extra="forbid")

    max_usd: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_retries_per_stage: int = Field(default=2, ge=0)


class RoleBind(BaseModel):
    """One role -> (provider, tier). Validated on load so bad YAML fails loudly."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    tier: Tier


class OpencodeSettings(BaseModel):
    """OpenCode Go/Zen connectivity. Keys come from env, never from YAML."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://opencode.ai/go/v1"
    api_key_env: str = "OPENCODE_API_KEY"


class TmuxSettings(BaseModel):
    """Supervision of process-backed harnesses (invariant #4)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: float = Field(default=1800.0, gt=0)
    # Isolate from the developer's default tmux server when set.
    socket_name: str | None = None


class GuardSettings(BaseModel):
    """Post-harness blast-radius checks run inside the stage worktree.

    ``checks`` is an allowlist of named checks (see :class:`GuardCheck`), never
    free-form shell. Default is syntax-only: broader tools would block stages on
    findings that predate the run.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    checks: list[GuardCheck] = Field(default_factory=lambda: [GuardCheck.PYTHON_SYNTAX])
    timeout_seconds: float = Field(default=120.0, gt=0)
    # False blocks the stage on failure but keeps the harness's work in place.
    revert_on_failure: bool = True


class HarnessSettings(BaseModel):
    """Operator-asserted harness invocation details.

    ``model_flag`` exists because AgentRail refuses to guess CLI flags: set it
    only if your harness build is known to accept one.
    """

    model_config = ConfigDict(extra="forbid")

    model_flag: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    provider_adapters: dict[Provider, str] = Field(default_factory=dict)


DEFAULT_ROLES: dict[Role, RoleBind] = {
    Role.PLAN: RoleBind(provider=Provider.ANTHROPIC, tier=Tier.DEEP),
    Role.RESEARCH: RoleBind(provider=Provider.OPENCODE, tier=Tier.DEEP),
    Role.CODE: RoleBind(provider=Provider.ZAI, tier=Tier.DEEP),
    Role.REVIEW: RoleBind(provider=Provider.OPENAI, tier=Tier.DEEP),
}


class Config(BaseModel):
    """Project configuration persisted to ``.agentrail/config.yaml``."""

    model_config = ConfigDict(extra="forbid")

    default_mode: Mode = Mode.PLAN
    default_profile: str = "balanced"
    harness: str = "jcode"
    budget: Budget = Field(default_factory=Budget)
    roles: dict[Role, RoleBind] = Field(default_factory=lambda: dict(DEFAULT_ROLES))
    opencode: OpencodeSettings = Field(default_factory=OpencodeSettings)
    tmux: TmuxSettings = Field(default_factory=TmuxSettings)
    guard: GuardSettings = Field(default_factory=GuardSettings)
    # Separate from the plain `harness` field above so existing config.yaml
    # files keep validating.
    harness_options: HarnessSettings = Field(default_factory=HarnessSettings)


def config_dir(root: Path) -> Path:
    return root / AGENTRAIL_DIR


def config_path(root: Path) -> Path:
    return config_dir(root) / CONFIG_FILENAME


def load_config(root: Path) -> Config:
    """Read and validate the project config; raise if it does not exist."""

    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"no AgentRail config at {path} (run `agentrail init`)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a mapping")
    return Config.model_validate(data)


def write_config(root: Path, config: Config) -> Path:
    """Serialize the config through the model (never hand-edit YAML)."""

    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def init_config(root: Path, *, overwrite: bool = False) -> tuple[Config, bool]:
    """Create a default config if absent. Returns ``(config, created)``."""

    path = config_path(root)
    if path.exists() and not overwrite:
        return load_config(root), False
    config = Config()
    write_config(root, config)
    return config, True
