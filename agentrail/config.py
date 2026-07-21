"""Project configuration — load/write ``.agentrail/config.yaml``.

Holds project-level defaults (mode, profile, harness). Validated on load via a
pydantic model so malformed config fails loudly rather than silently.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from agentrail.models import Mode

AGENTRAIL_DIR = ".agentrail"
CONFIG_FILENAME = "config.yaml"


class Config(BaseModel):
    """Project configuration persisted to ``.agentrail/config.yaml``."""

    model_config = ConfigDict(extra="forbid")

    default_mode: Mode = Mode.PLAN
    default_profile: str = "balanced"
    harness: str = "jcode"


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
