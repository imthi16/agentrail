"""Intent Lock persistence — write/read ``.agentrail/intent-lock.<stage-id>.yaml``.

An Intent Lock is the scope contract for a stage. It is stored as YAML, its
canonical form is hashed with SHA-256, and that hash is recorded on the stage in
``workflow.yaml``. Every gated action re-verifies the live lock against the
recorded hash (see :mod:`agentrail.policies.engine`) so tampering is detected.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentrail.config import config_dir
from agentrail.models import IntentLock, Stage, Workflow


def _lock_filename(stage_id: str) -> str:
    return f"intent-lock.{stage_id}.yaml"


def lock_path(root: Path, stage_id: str) -> Path:
    return config_dir(root) / _lock_filename(stage_id)


def write_lock(root: Path, stage_id: str, lock: IntentLock) -> tuple[Path, str]:
    """Persist an Intent Lock and return ``(path, sha256)``.

    Serializes through the model (never hand-edited YAML). The returned hash is
    computed over the canonical (sorted) form, matching what the engine checks.
    """

    path = lock_path(root, stage_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = lock.model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path, lock.sha256()


def read_lock(root: Path, stage_id: str) -> IntentLock:
    """Load and validate a stage's Intent Lock from disk."""

    path = lock_path(root, stage_id)
    if not path.exists():
        raise FileNotFoundError(f"no Intent Lock at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Intent Lock at {path} must be a mapping")
    return IntentLock.model_validate(data)


def bind_lock_to_stage(root: Path, stage: Stage, lock: IntentLock) -> str:
    """Write the lock and record its hash on ``stage.intent_lock_hash``.

    Mutates the given :class:`Stage` in place and returns the recorded hash. The
    caller is responsible for persisting the owning workflow afterward.
    """

    _, digest = write_lock(root, stage.id, lock)
    stage.intent_lock_hash = digest
    return digest


def verify_stage_lock(root: Path, stage: Stage) -> bool:
    """True iff the on-disk lock still matches the hash recorded on the stage.

    Fails closed: a missing recorded hash or a missing/mismatched lock file
    returns ``False`` rather than raising.
    """

    if stage.intent_lock_hash is None:
        return False
    try:
        lock = read_lock(root, stage.id)
    except (FileNotFoundError, ValueError):
        return False
    return lock.sha256() == stage.intent_lock_hash


def verify_workflow_locks(root: Path, workflow: Workflow) -> dict[str, bool]:
    """Verify every stage that declares an Intent Lock hash.

    Returns ``{stage_id: ok}`` only for stages that recorded a hash.
    """

    return {
        stage.id: verify_stage_lock(root, stage)
        for stage in workflow.stages
        if stage.intent_lock_hash is not None
    }
