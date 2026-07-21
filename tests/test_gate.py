"""Subprocess-interception gate: real enforcement of shell/edit actions."""

from __future__ import annotations

from pathlib import Path

from agentrail.events import EventLog
from agentrail.models import IntentLock, Mode
from agentrail.policies import Action, Decision, PolicyGate, PolicyResult


def _gate(tmp_path: Path, mode: Mode, **kw: object) -> PolicyGate:
    return PolicyGate(
        workspace_root=tmp_path,
        mode=mode,
        workflow_id="wf-1",
        event_log=EventLog(tmp_path),
        **kw,  # type: ignore[arg-type]
    )


def test_plan_mode_blocks_shell_and_edit(tmp_path: Path) -> None:
    gate = _gate(tmp_path, Mode.PLAN)
    out = gate.run_shell("echo hi")
    assert out.decision is Decision.DENY
    assert out.performed is False

    edit = gate.write_file("a.txt", "data")
    assert edit.decision is Decision.DENY
    assert not (tmp_path / "a.txt").exists()


def test_auto_shell_runs_when_allowed(tmp_path: Path) -> None:
    lock = IntentLock(shell_allow=["echo"])
    gate = _gate(tmp_path, Mode.AUTO, intent_lock=lock, recorded_hash=lock.sha256())
    out = gate.run_shell("echo hello")
    assert out.decision is Decision.ALLOW
    assert out.performed is True
    assert out.returncode == 0
    assert "hello" in out.stdout


def test_accept_edits_writes_file_within_scope(tmp_path: Path) -> None:
    lock = IntentLock(allowed_paths=["src"])
    gate = _gate(tmp_path, Mode.ACCEPT_EDITS, intent_lock=lock, recorded_hash=lock.sha256())
    out = gate.write_file("src/app.py", "print('x')\n")
    assert out.decision is Decision.ALLOW
    assert out.performed is True
    assert (tmp_path / "src" / "app.py").read_text() == "print('x')\n"


def test_ask_denied_by_default_approver(tmp_path: Path) -> None:
    gate = _gate(tmp_path, Mode.MANUAL)
    out = gate.write_file("a.txt", "data")
    assert out.decision is Decision.ASK
    assert out.performed is False
    assert not (tmp_path / "a.txt").exists()


def test_ask_approved_by_custom_approver_runs(tmp_path: Path) -> None:
    def approve(action: Action, result: PolicyResult) -> bool:
        return True

    gate = _gate(tmp_path, Mode.MANUAL, approver=approve)
    out = gate.write_file("a.txt", "data")
    assert out.decision is Decision.ASK
    assert out.performed is True
    assert (tmp_path / "a.txt").read_text() == "data"


def test_hash_mismatch_blocks_execution(tmp_path: Path) -> None:
    lock = IntentLock(shell_allow=["echo"])
    gate = _gate(tmp_path, Mode.AUTO, intent_lock=lock, recorded_hash="deadbeef")
    out = gate.run_shell("echo hello")
    assert out.decision is Decision.DENY
    assert out.performed is False


def test_every_action_is_logged(tmp_path: Path) -> None:
    gate = _gate(tmp_path, Mode.PLAN)
    gate.run_shell("echo hi")
    gate.write_file("a.txt", "x")

    events = EventLog(tmp_path).read()
    decisions = [e for e in events if e.type == "policy.decision"]
    assert len(decisions) == 2
    assert all(e.trace_id == gate.trace_id for e in decisions)
    assert {e.attributes["action_type"] for e in decisions} == {"shell", "edit"}


def test_denied_shell_never_executes(tmp_path: Path) -> None:
    marker = tmp_path / "created.txt"
    gate = _gate(tmp_path, Mode.PLAN)
    out = gate.run_shell(f"touch {marker}")
    assert out.performed is False
    assert not marker.exists()
