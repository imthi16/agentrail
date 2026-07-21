"""Policy engine: mode matrix, deny-wins, path escapes, hash fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrail.models import IntentLock, Mode
from agentrail.policies import (
    Action,
    ActionType,
    Decision,
    LockView,
    PolicyEngine,
    decide,
    verify_lock_hash,
)


def _view(root: Path, lock: IntentLock | None = None) -> LockView:
    return LockView.build(root, lock)


def _edit(path: str) -> Action:
    return Action(type=ActionType.EDIT, path=path)


def _shell(cmd: str) -> Action:
    return Action(type=ActionType.SHELL, command=cmd)


def _read(path: str) -> Action:
    return Action(type=ActionType.READ, path=path)


def test_plan_mode_is_read_only(tmp_path: Path) -> None:
    view = _view(tmp_path)
    assert decide(Mode.PLAN, view, _read("a.py")).decision is Decision.ALLOW
    assert decide(Mode.PLAN, view, _edit("a.py")).decision is Decision.DENY
    assert decide(Mode.PLAN, view, _shell("ls")).decision is Decision.DENY


def test_manual_mode_asks_for_edit_and_shell(tmp_path: Path) -> None:
    view = _view(tmp_path)
    assert decide(Mode.MANUAL, view, _edit("a.py")).decision is Decision.ASK
    assert decide(Mode.MANUAL, view, _shell("pytest")).decision is Decision.ASK


def test_accept_edits_auto_approves_edits_but_asks_shell(tmp_path: Path) -> None:
    view = _view(tmp_path)
    assert decide(Mode.ACCEPT_EDITS, view, _edit("a.py")).decision is Decision.ALLOW
    assert decide(Mode.ACCEPT_EDITS, view, _shell("pytest")).decision is Decision.ASK


def test_auto_edit_requires_lock_scope(tmp_path: Path) -> None:
    # No lock -> auto edits are denied (fail closed).
    assert decide(Mode.AUTO, _view(tmp_path), _edit("a.py")).decision is Decision.DENY

    lock = IntentLock(allowed_paths=["src"])
    view = _view(tmp_path, lock)
    assert decide(Mode.AUTO, view, _edit("src/a.py")).decision is Decision.ALLOW
    assert decide(Mode.AUTO, view, _edit("other/a.py")).decision is Decision.DENY


def test_deny_wins_over_allow(tmp_path: Path) -> None:
    lock = IntentLock(allowed_paths=["src"], denied_paths=["src/secrets"])
    view = _view(tmp_path, lock)
    assert decide(Mode.ACCEPT_EDITS, view, _edit("src/app.py")).decision is Decision.ALLOW
    assert decide(Mode.ACCEPT_EDITS, view, _edit("src/secrets/key.pem")).decision is Decision.DENY


def test_path_escape_is_denied(tmp_path: Path) -> None:
    view = _view(tmp_path, IntentLock(allowed_paths=["src"]))
    assert decide(Mode.ACCEPT_EDITS, view, _edit("../../etc/passwd")).decision is Decision.DENY


def test_shell_allow_and_deny_prefixes(tmp_path: Path) -> None:
    lock = IntentLock(shell_allow=["pytest", "git status"], shell_deny=["rm -rf"])
    view = _view(tmp_path, lock)
    assert decide(Mode.AUTO, view, _shell("pytest -q")).decision is Decision.ALLOW
    assert decide(Mode.AUTO, view, _shell("git status --short")).decision is Decision.ALLOW
    assert decide(Mode.AUTO, view, _shell("npm install")).decision is Decision.DENY
    # deny wins even though nothing is in allow for this prefix
    assert decide(Mode.AUTO, view, _shell("rm -rf /")).decision is Decision.DENY


def test_shell_deny_wins_in_every_mode(tmp_path: Path) -> None:
    view = _view(tmp_path, IntentLock(shell_deny=["rm -rf"]))
    for mode in (Mode.MANUAL, Mode.ACCEPT_EDITS, Mode.AUTO):
        assert decide(mode, view, _shell("rm -rf /")).decision is Decision.DENY


def test_empty_and_missing_inputs_fail_closed(tmp_path: Path) -> None:
    view = _view(tmp_path)
    assert decide(Mode.AUTO, view, Action(type=ActionType.SHELL, command="")).decision is (
        Decision.DENY
    )
    assert decide(Mode.MANUAL, view, Action(type=ActionType.EDIT)).decision is Decision.DENY


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    lock = IntentLock(allowed_paths=["src"])
    good = lock.sha256()
    assert verify_lock_hash(lock, good) is True

    engine = PolicyEngine(
        workspace_root=tmp_path,
        mode=Mode.ACCEPT_EDITS,
        intent_lock=lock,
        recorded_hash="deadbeef",
    )
    assert engine.check(_edit("src/a.py")).decision is Decision.DENY


def test_engine_allows_when_hash_matches(tmp_path: Path) -> None:
    lock = IntentLock(allowed_paths=["src"])
    engine = PolicyEngine(
        workspace_root=tmp_path,
        mode=Mode.ACCEPT_EDITS,
        intent_lock=lock,
        recorded_hash=lock.sha256(),
    )
    assert engine.check(_edit("src/a.py")).decision is Decision.ALLOW


def test_intent_lock_hash_is_order_independent() -> None:
    a = IntentLock(allowed_paths=["src", "tests"], shell_allow=["pytest", "ruff"])
    b = IntentLock(allowed_paths=["tests", "src"], shell_allow=["ruff", "pytest"])
    assert a.sha256() == b.sha256()


@pytest.mark.parametrize("mode", list(Mode))
def test_reads_allowed_in_all_modes(tmp_path: Path, mode: Mode) -> None:
    assert decide(mode, _view(tmp_path), _read("a.py")).decision is Decision.ALLOW
