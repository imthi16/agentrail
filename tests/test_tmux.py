"""Tmux supervisor: real spawn/capture/recover, with graceful skip."""

from __future__ import annotations

import uuid

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
