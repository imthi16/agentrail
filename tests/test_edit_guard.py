"""Semantic Edit Guard: read-before-edit + post-edit auto-revert/retry."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrail.checkpoints import EditGuardError, SemanticEditGuard


def _writer(path: Path, content: str, _ctx: str) -> str:
    path.write_text(content, encoding="utf-8")
    return content


def test_blind_overwrite_of_existing_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    guard = SemanticEditGuard()
    with pytest.raises(EditGuardError):
        guard.guarded_edit(target, "new\n", _writer)


def test_read_before_edit_allows_edit(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    guard = SemanticEditGuard()
    guard.read(target)
    result = guard.guarded_edit(target, "new\n", _writer)
    assert result.applied is True
    assert target.read_text() == "new\n"


def test_new_file_needs_no_prior_read(tmp_path: Path) -> None:
    target = tmp_path / "new.py"
    guard = SemanticEditGuard()
    result = guard.guarded_edit(target, "print()\n", _writer)
    assert result.applied is True


def test_failing_check_triggers_revert_and_retry(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    calls = {"n": 0}

    def flaky_check(_path: Path) -> tuple[bool, str]:
        calls["n"] += 1
        return (calls["n"] >= 2, "syntax error near line 1")

    reverts = {"n": 0}

    def revert() -> None:
        reverts["n"] += 1

    guard = SemanticEditGuard(checks=[flaky_check], max_retries=1)
    result = guard.guarded_edit(target, "code\n", _writer, revert=revert)
    assert result.applied is True
    assert result.attempts == 2
    assert result.reverted is True


def test_persistent_failure_gives_up_with_error_log(tmp_path: Path) -> None:
    target = tmp_path / "a.py"

    def always_fail(_path: Path) -> tuple[bool, str]:
        return False, "type collision outside allowed_paths"

    guard = SemanticEditGuard(checks=[always_fail], max_retries=1)
    result = guard.guarded_edit(target, "code\n", _writer)
    assert result.applied is False
    assert result.attempts == 2
    assert "type collision outside allowed_paths" in result.failures[-1]


def test_retry_context_carries_error_log(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    seen_contexts: list[str] = []

    def capture(path: Path, content: str, ctx: str) -> str:
        seen_contexts.append(ctx)
        path.write_text(content, encoding="utf-8")
        return content

    def fail_once(_path: Path) -> tuple[bool, str]:
        return len(seen_contexts) >= 2, "boom"

    guard = SemanticEditGuard(checks=[fail_once], max_retries=1)
    guard.guarded_edit(target, "x\n", capture)
    assert seen_contexts[0] == ""
    assert "boom" in seen_contexts[1]
