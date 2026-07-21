"""Intent Lock persistence: write/read, bind-to-stage, tamper detection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentrail.models import IntentLock, Stage, Workflow
from agentrail.policies import (
    bind_lock_to_stage,
    lock_path,
    read_lock,
    verify_stage_lock,
    verify_workflow_locks,
    write_lock,
)


def _lock() -> IntentLock:
    return IntentLock(allowed_paths=["src"], shell_allow=["pytest"])


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    lock = _lock()
    path, digest = write_lock(tmp_path, "login", lock)

    assert path == tmp_path / ".agentrail" / "intent-lock.login.yaml"
    assert path.exists()
    assert digest == lock.sha256()
    assert read_lock(tmp_path, "login").sha256() == digest


def test_bind_records_hash_on_stage(tmp_path: Path) -> None:
    stage = Stage(id="login", title="Login")
    digest = bind_lock_to_stage(tmp_path, stage, _lock())

    assert stage.intent_lock_hash == digest
    assert verify_stage_lock(tmp_path, stage) is True


def test_tampered_lock_is_detected(tmp_path: Path) -> None:
    stage = Stage(id="login", title="Login")
    bind_lock_to_stage(tmp_path, stage, _lock())

    # Hand-edit the on-disk lock to widen scope after the hash was recorded.
    path = lock_path(tmp_path, "login")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["allowed_paths"].append("/etc")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert verify_stage_lock(tmp_path, stage) is False


def test_missing_lock_or_hash_fails_closed(tmp_path: Path) -> None:
    no_hash = Stage(id="login", title="Login")
    assert verify_stage_lock(tmp_path, no_hash) is False

    ghost = Stage(id="ghost", title="Ghost", intent_lock_hash="deadbeef")
    assert verify_stage_lock(tmp_path, ghost) is False


def test_read_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_lock(tmp_path, "nope")


def test_verify_workflow_locks_only_checks_bound_stages(tmp_path: Path) -> None:
    login = Stage(id="login", title="Login")
    logout = Stage(id="logout", title="Logout")
    bind_lock_to_stage(tmp_path, login, _lock())
    workflow = Workflow(goal="g", stages=[login, logout])

    results = verify_workflow_locks(tmp_path, workflow)
    assert results == {"login": True}
