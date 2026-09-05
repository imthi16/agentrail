"""Tmux supervisor: real spawn/capture/recover, with graceful skip."""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentrail.tmux import (
    PaneHandle,
    TmuxSupervisor,
    session_name,
    tmux_available,
)

pytestmark = pytest.mark.skipif(not tmux_available(), reason="tmux/libtmux not available")


@pytest.fixture()
def stage_id() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def supervisor() -> TmuxSupervisor:
    return TmuxSupervisor()


def test_session_name_helper() -> None:
    assert session_name("login") == "agentrail-login"


def test_run_captures_output_before_marker(supervisor: TmuxSupervisor, stage_id: str) -> None:
    try:
        handle, output = supervisor.run(stage_id, "echo agentrail-hello", timeout=15.0)
        assert isinstance(handle, PaneHandle)
        assert handle.pane_id.startswith("%") or handle.pane_id != ""
        assert any("agentrail-hello" in line for line in output)
    finally:
        supervisor.kill(stage_id)


def test_ensure_session_is_idempotent(supervisor: TmuxSupervisor, stage_id: str) -> None:
    try:
        s1 = supervisor.ensure_session(stage_id)
        s2 = supervisor.ensure_session(stage_id)
        assert s1.session_name == s2.session_name == session_name(stage_id)
    finally:
        supervisor.kill(stage_id)


def test_kill_is_idempotent(supervisor: TmuxSupervisor, stage_id: str) -> None:
    supervisor.ensure_session(stage_id)
    supervisor.kill(stage_id)
    supervisor.kill(stage_id)  # must not raise
    assert not supervisor.server.sessions.filter(session_name=session_name(stage_id))


def test_pane_handle_serializes() -> None:
    handle = PaneHandle(session_id="$1", window_id="@2", pane_id="%3")
    assert handle.as_dict() == {"session_id": "$1", "window_id": "@2", "pane_id": "%3"}


@pytest.fixture()
def isolated() -> Iterator[TmuxSupervisor]:
    """A supervisor on its own tmux server, never the developer's default one."""

    sock = f"agentrail-test-{uuid.uuid4().hex[:8]}"
    supervisor = TmuxSupervisor(socket_name=sock)
    try:
        yield supervisor
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, check=False)


def test_run_result_reports_zero_exit_code(isolated: TmuxSupervisor, stage_id: str) -> None:
    result = isolated.run_result(stage_id, "echo agentrail-ok", timeout=20.0)
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.succeeded is True
    assert any("agentrail-ok" in line for line in result.output)


@pytest.mark.parametrize(("command", "expected"), [("exit 3", 3), ("false", 1)])
def test_run_result_reports_nonzero_exit_code(
    isolated: TmuxSupervisor, stage_id: str, command: str, expected: int
) -> None:
    """`exit` must not skip the status line -- that would look like a timeout."""

    result = isolated.run_result(stage_id, command, timeout=20.0)
    assert result.timed_out is False
    assert result.exit_code == expected
    assert result.succeeded is False


def test_run_result_runs_in_the_given_cwd(
    isolated: TmuxSupervisor, stage_id: str, tmp_path: Path
) -> None:
    target = tmp_path / "stage-worktree"
    target.mkdir()
    result = isolated.run_result(stage_id, "pwd", cwd=target, timeout=20.0)
    assert result.exit_code == 0
    # tmux hard-wraps at the pane width and pytest's tmp_path is long, so the
    # printed path can be split across captured lines: join before matching.
    assert "stage-worktree" in "".join(result.output)


def test_run_result_times_out_without_raising(isolated: TmuxSupervisor, stage_id: str) -> None:
    result = isolated.run_result(stage_id, "sleep 30", timeout=2.0)
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.succeeded is False


def test_run_result_waits_for_a_slow_command(isolated: TmuxSupervisor, stage_id: str) -> None:
    """Regression test for the completion-marker race.

    If the marker were ever matched against the echoed command line rather than
    real output, this returns almost instantly and reports success before the
    harness has finished -- the worst possible failure mode here.
    """

    start = time.monotonic()
    result = isolated.run_result(stage_id, "sleep 2; echo finished", timeout=25.0)
    elapsed = time.monotonic() - start

    assert elapsed >= 1.5
    assert result.exit_code == 0
    assert any("finished" in line for line in result.output)
