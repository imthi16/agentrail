"""Typed models — single source of truth for YAML (de)serialization.

Pydantic v2 models for the AgentRail control plane. Subsystem code paths
(git, tmux, policies, ...) construct and read these; they never hand-edit YAML.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class Mode(StrEnum):
    """Capability modes (enforced in the policies engine, not here)."""

    PLAN = "plan"
    MANUAL = "manual"
    ACCEPT_EDITS = "accept_edits"
    AUTO = "auto"


class WorkflowStatus(StrEnum):
    """Lifecycle status of a whole workflow."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(StrEnum):
    """Lifecycle status of a single stage."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class Tier(StrEnum):
    """Effort tier for the model picker (profiles)."""

    DEEP = "deep"
    BALANCED = "balanced"
    FAST = "fast"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Base(BaseModel):
    """Shared config: forbid unknown fields so bad YAML fails loudly."""

    model_config = ConfigDict(extra="forbid")


class WorktreeRef(_Base):
    """Where a stage's isolated git worktree lives (workspaces spec)."""

    path: str
    branch: str
    base: str


class PullRequestRef(_Base):
    """A stage's draft PR coordinates (git spec). ``number`` is None until opened."""

    number: int | None = None
    base: str
    head: str
    url: str | None = None
    draft: bool = True


class Stage(_Base):
    """A single unit of work in the DAG. One stage = one worktree = one branch."""

    id: str = Field(pattern=_ID_PATTERN)
    title: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: StageStatus = StageStatus.PENDING
    intent_lock_hash: str | None = None
    worktree: WorktreeRef | None = None
    pull_request: PullRequestRef | None = None
    checkpoints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_self_dependency(self) -> Stage:
        if self.id in self.depends_on:
            raise ValueError(f"stage {self.id!r} cannot depend on itself")
        return self


class Profile(_Base):
    """Resolved model routing: (provider, tier) -> concrete model + thinking param.

    Volatile model IDs live in config, never hardcoded in reproducible paths.
    """

    name: str
    provider: str
    tier: Tier
    model_id: str
    thinking_param: str | None = None


class IntentLock(_Base):
    """Scope contract for a stage. Deny rules always win over allow rules."""

    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    shell_allow: list[str] = Field(default_factory=list)
    shell_deny: list[str] = Field(default_factory=list)

    def canonical(self) -> dict[str, list[str]]:
        """Deterministic, sorted representation used for hashing."""

        return {
            "allowed_paths": sorted(self.allowed_paths),
            "denied_paths": sorted(self.denied_paths),
            "shell_allow": sorted(self.shell_allow),
            "shell_deny": sorted(self.shell_deny),
        }

    def sha256(self) -> str:
        """SHA-256 over the canonical form, recorded next to the stage."""

        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Checkpoint(_Base):
    """File-based rollback snapshot reference (no secrets stored inline)."""

    id: str = Field(pattern=_ID_PATTERN)
    stage_id: str
    git_head: str
    changes_patch: str | None = None
    manifest: dict[str, str] = Field(default_factory=dict)
    tmux: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Workflow(_Base):
    """The workflow + stage DAG persisted to .agentrail/workflow.yaml."""

    goal: str
    mode: Mode = Mode.PLAN
    status: WorkflowStatus = WorkflowStatus.AWAITING_APPROVAL
    stages: list[Stage] = Field(default_factory=list)
    workflow_id: str = Field(default_factory=lambda: _utcnow().strftime("wf-%Y%m%d%H%M%S"))
    created_at: datetime = Field(default_factory=_utcnow)

    def stage_ids(self) -> set[str]:
        return {stage.id for stage in self.stages}
