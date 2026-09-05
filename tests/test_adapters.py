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
    assert set(adapter_names()) == {"jcode", "opencode", "claude-code", "codex", "gemini"}


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


def test_jcode_argv_unchanged_without_a_configured_model_flag() -> None:
    """AgentRail never guesses a harness flag: default argv must not change."""

    adapter = JcodeAdapter()
    argv = adapter.build_argv("do it", mode=Mode.ACCEPT_EDITS, model_id="glm-5.3")
    assert argv == ["jcode", "run", "do it"]
    assert adapter.capabilities().model_selection is False


def test_jcode_injects_operator_configured_model_flag() -> None:
    adapter = JcodeAdapter(model_flag="--model", extra_args=["--quiet"])
    argv = adapter.build_argv("do it", mode=Mode.ACCEPT_EDITS, model_id="glm-5.3")
    assert argv == ["jcode", "run", "--quiet", "--model", "glm-5.3", "do it"]
    assert adapter.capabilities().model_selection is True


def test_jcode_omits_model_flag_when_no_model_is_routed() -> None:
    adapter = JcodeAdapter(model_flag="--model")
    assert adapter.build_argv("x", mode=Mode.ACCEPT_EDITS, model_id=None) == ["jcode", "run", "x"]


@pytest.mark.parametrize(
    ("name", "process_backed", "edits_files"),
    [
        ("jcode", True, True),
        ("opencode", False, False),
        ("claude-code", True, True),
        ("codex", True, True),
        ("gemini", True, True),
    ],
)
def test_every_adapter_declares_supervision_capabilities(
    name: str, process_backed: bool, edits_files: bool
) -> None:
    caps = get_adapter(name).capabilities()
    assert caps.process_backed is process_backed
    assert caps.edits_files is edits_files


def test_opencode_declares_it_applies_the_routed_model() -> None:
    """It really does send `model` in the POST body, unlike the CLI harnesses."""

    assert get_adapter("opencode").capabilities().model_selection is True
