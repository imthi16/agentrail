"""Concrete Semantic Edit Guard checks — an allowlist, not free-form shell.

Every check is **scoped to the files changed since the stage's checkpoint**.
Whole-worktree checks would block stages on findings that predate the run, which
is why no checks ever shipped enabled; scoping means pre-existing breakage in
untouched files cannot fire, so no baseline/regression machinery is needed while
the default stays syntax-only.

**Failure model (deliberate asymmetry).** These checks *fail open when they
cannot check* (tool missing, timeout, no changed files) and *fail closed on a
real failure*. That is correct because the guard is a QUALITY gate, not a
security boundary — the security boundary is ``PolicyGate``, which fails closed.
Do not "fix" this asymmetry.

**Why an allowlist.** The guard runs commands outside ``PolicyGate`` with
control-plane privileges, and ``.agentrail/config.yaml`` is writable from inside
a stage worktree. Free-form commands in config would let a harness that can
write files escape its own Intent Lock through the subsystem meant to catch bad
edits. ``pytest`` is opt-in for a related reason: it imports the worktree's
``conftest.py`` into the control-plane process.

If ruff/mypy/pytest are ever enabled by default, per-check baseline comparison
becomes a prerequisite first.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentrail.checkpoints.guard import Check, SemanticEditGuard
from agentrail.models import GuardCheck

# Truncate check output fed back into a retry prompt / event attributes.
_MAX_MESSAGE_CHARS = 4000


class CommandRunner(Protocol):
    """Injected so unit tests never shell out to a real toolchain."""

    def __call__(
        self, argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]: ...


def default_command_runner(
    argv: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - allowlisted argv, no shell
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


class GitRunner(Protocol):
    def __call__(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]: ...


def default_git_runner(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git argv, no shell
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def worktree_root(path: Path) -> Path:
    """Normalize the dual meaning of a ``Check``'s argument.

    ``SemanticEditGuard.guarded_edit`` passes the edited *file*; ``run_checks``
    (the run-loop path) passes the *worktree root*. Checks must accept both.
    """

    return path if path.is_dir() else path.parent


def changed_files(worktree: Path, *, runner: GitRunner = default_git_runner) -> list[Path]:
    """Files modified or added since HEAD, as paths relative to ``worktree``."""

    names: list[str] = []
    for argv in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        completed = runner(argv, worktree)
        if completed.returncode != 0:
            continue
        names.extend(line.strip() for line in completed.stdout.splitlines() if line.strip())

    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    return [worktree / name for name in seen]


def changed_python_files(worktree: Path, *, runner: GitRunner = default_git_runner) -> list[Path]:
    return [p for p in changed_files(worktree, runner=runner) if p.suffix == ".py"]


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    return text[:_MAX_MESSAGE_CHARS] + "\n... (truncated)"


@dataclass
class PythonSyntaxCheck:
    """Parse every changed ``*.py`` with :func:`ast.parse`.

    Pure stdlib and milliseconds fast, so it works in any repo without assuming
    a toolchain. This is the only check enabled by default: "the harness did not
    leave unparseable Python behind" is a true blast-radius signal everywhere.
    """

    name: str = "python_syntax"
    git_runner: GitRunner = field(default=default_git_runner)

    def __call__(self, path: Path) -> tuple[bool, str]:
        root = worktree_root(path)
        targets = (
            [path]
            if path.is_file() and path.suffix == ".py"
            else changed_python_files(root, runner=self.git_runner)
        )
        failures: list[str] = []
        for target in targets:
            if not target.exists():
                continue  # deleted in this stage; nothing to parse
            try:
                ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
            except SyntaxError as exc:
                failures.append(f"{target}: {exc.msg} (line {exc.lineno})")
            except (OSError, UnicodeDecodeError, ValueError):
                # Unreadable/undecodable: cannot check -> fail open, never block.
                continue
        if failures:
            return False, _truncate("\n".join(failures))
        return True, ""


@dataclass
class CommandCheck:
    """One allowlisted tool invocation, run inside the stage worktree."""

    name: str
    argv: tuple[str, ...]
    per_file: bool = True
    # Only run when the project actually configures this tool, so we never
    # carpet-bomb a repo with a tool's default rules.
    config_markers: tuple[str, ...] = ()
    timeout: float = 120.0
    runner: CommandRunner = field(default=default_command_runner)
    git_runner: GitRunner = field(default=default_git_runner)

    def __call__(self, path: Path) -> tuple[bool, str]:
        root = worktree_root(path)

        targets = changed_python_files(root, runner=self.git_runner)
        if self.per_file and not targets:
            return True, ""  # nothing this stage touched; skip entirely

        if shutil.which(self.argv[0]) is None:
            return True, ""  # tool absent is not a code defect

        if self.config_markers and not any((root / m).exists() for m in self.config_markers):
            return True, ""

        argv = list(self.argv)
        if self.per_file:
            argv.extend(str(t.relative_to(root)) for t in targets)

        try:
            completed = self.runner(argv, root, self.timeout)
        except subprocess.TimeoutExpired:
            # Cannot conclude -> fail open. A hung tool must never trigger an
            # irreversible `git reset --hard`.
            return True, ""
        except OSError:
            return True, ""

        if completed.returncode == 0:
            return True, ""
        output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        return False, _truncate(f"[{self.name}] exit {completed.returncode}\n{output}")


def python_syntax_check() -> Check:
    return PythonSyntaxCheck()


def ruff_check() -> Check:
    return CommandCheck(
        name="ruff",
        argv=("ruff", "check", "--no-cache"),
        per_file=True,
        config_markers=("ruff.toml", ".ruff.toml", "pyproject.toml"),
    )


def ruff_format_check() -> Check:
    return CommandCheck(
        name="ruff-format",
        argv=("ruff", "format", "--check", "--no-cache"),
        per_file=True,
        config_markers=("ruff.toml", ".ruff.toml", "pyproject.toml"),
    )


def mypy_check() -> Check:
    return CommandCheck(
        name="mypy",
        argv=("mypy",),
        per_file=True,
        config_markers=("mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"),
        timeout=300.0,
    )


def pytest_check() -> Check:
    """Opt-in only: imports the worktree's ``conftest.py`` into this process."""

    return CommandCheck(
        name="pytest",
        argv=("pytest", "-q", "-x", "-p", "no:cacheprovider"),
        per_file=False,
        config_markers=("pyproject.toml", "pytest.ini", "tox.ini"),
        timeout=600.0,
    )


CHECK_FACTORIES: dict[GuardCheck, Callable[[], Check]] = {
    GuardCheck.PYTHON_SYNTAX: python_syntax_check,
    GuardCheck.RUFF: ruff_check,
    GuardCheck.RUFF_FORMAT: ruff_format_check,
    GuardCheck.MYPY: mypy_check,
    GuardCheck.PYTEST: pytest_check,
}


def build_checks(names: Sequence[GuardCheck]) -> list[Check]:
    """Resolve configured check names to callables, cheapest first."""

    checks: list[Check] = []
    for name in names:
        factory = CHECK_FACTORIES.get(name)
        if factory is None:  # pragma: no cover - GuardCheck is a closed enum
            raise ValueError(f"unknown guard check: {name!r}")
        checks.append(factory())
    return checks


def build_edit_guard(
    *,
    enabled: bool,
    names: Sequence[GuardCheck],
    max_retries: int = 1,
) -> SemanticEditGuard | None:
    """Build the guard from config. ``None`` when disabled or empty."""

    if not enabled:
        return None
    checks = build_checks(names)
    if not checks:
        return None
    return SemanticEditGuard(checks=checks, max_retries=max_retries)
