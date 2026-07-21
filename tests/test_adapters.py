"""Harness adapters: interface parity, jcode default, argv shapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrail.adapters import (
    ClaudeCodeAdapter,
    CodexAdapter,
    GeminiAdapter,
    JcodeAdapter,
    adapter_names,
    get_adapter,
)
from agentrail.models import Mode


def test_default_adapter_is_jcode() -> None:
    adapter = get_adapter()
    assert isinstance(adapter, JcodeAdapter)
    assert adapter.name == "jcode"


def test_all_adapters_registered() -> None:
    assert set(adapter_names()) == {"jcode", "claude-code", "codex", "gemini"}


def test_unknown_adapter_raises() -> None:
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_jcode_argv_is_non_interactive_run() -> None:
    argv = JcodeAdapter().build_argv("do the thing", mode=Mode.AUTO, model_id=None)
    assert argv == ["jcode", "run", "do the thing"]


def test_jcode_resume_argv() -> None:
    assert JcodeAdapter().resume_argv("brave-otter") == ["jcode", "--resume", "brave-otter"]


def test_jcode_capabilities() -> None:
    caps = JcodeAdapter().capabilities()
    assert caps.non_interactive is True
    assert caps.swarm is True
    assert "claude" in caps.providers


def test_jcode_dry_run_start_does_not_execute(tmp_path: Path) -> None:
    handle = JcodeAdapter().start("hi", mode=Mode.PLAN, model_id=None, cwd=tmp_path, dry_run=True)
    assert handle.started is False
    assert handle.argv == ["jcode", "run", "hi"]


@pytest.mark.parametrize(
    ("adapter", "expected_head"),
    [
        (ClaudeCodeAdapter(), "claude"),
        (CodexAdapter(), "codex"),
        (GeminiAdapter(), "gemini"),
    ],
)
def test_stub_adapters_build_argv(adapter: object, expected_head: str) -> None:
    argv = adapter.build_argv("prompt", mode=Mode.PLAN, model_id=None)  # type: ignore[attr-defined]
    assert argv[0] == expected_head
    assert "prompt" in argv
