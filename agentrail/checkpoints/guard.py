"""Semantic Edit Guard — read-before-edit + post-edit checks with auto-revert.

Enforced together with the policies engine (checkpoints/CLAUDE.md):

- **Read-before-edit:** an edit must be based on the current file content;
  blind overwrites of unread files/symbols are rejected.
- **Post-edit:** run injected checks (LSP diagnostics, formatters, targeted
  tests). If a check fails (syntax error, type collision, failing test) the
  edit is auto-reverted from the last checkpoint and retried with the error log
  attached to the prompt context.

Side effects (running checks, applying the edit, reverting) are injected so the
guard is deterministic and unit-testable with no real toolchain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# A check returns (ok, message). message is the error log fed back on retry.
Check = Callable[[Path], tuple[bool, str]]
# Apply an edit; receives (path, content, retry_context) -> new file content.
EditFn = Callable[[Path, str, str], str]


class EditGuardError(RuntimeError):
    """Raised when an edit is rejected outright (e.g. blind overwrite)."""


@dataclass
class GuardResult:
    """Outcome of a guarded edit."""

    applied: bool
    attempts: int
    reverted: bool
    failures: list[str] = field(default_factory=list)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SemanticEditGuard:
    """Guards edits with read-before-edit and post-edit blast-radius checks."""

    checks: list[Check] = field(default_factory=list)
    max_retries: int = 1
    _seen: dict[Path, str] = field(default_factory=dict, init=False, repr=False)

    def read(self, path: Path) -> str:
        """Read current content and remember its digest for later verification."""

        content = path.read_text(encoding="utf-8") if path.exists() else ""
        self._seen[path.resolve()] = _digest(content)
        return content

    def _was_read(self, path: Path, expected_prior: str | None) -> bool:
        key = path.resolve()
        if expected_prior is not None:
            return self._seen.get(key) == _digest(expected_prior)
        return key in self._seen

    def guarded_edit(
        self,
        path: Path,
        new_content: str,
        apply_edit: EditFn,
        *,
        expected_prior: str | None = None,
        revert: Callable[[], None] | None = None,
    ) -> GuardResult:
        """Apply ``apply_edit`` under read-before-edit + post-edit guarding.

        ``apply_edit(path, content, retry_context)`` performs the write and
        returns the content actually written. On any failed check the guard
        calls ``revert`` (if provided) and retries up to ``max_retries`` with
        the accumulated error log as ``retry_context``.
        """

        if path.exists() and not self._was_read(path, expected_prior):
            raise EditGuardError(
                f"read-before-edit violated: {path} was not read before editing"
            )

        failures: list[str] = []
        retry_context = ""
        attempts = 0
        reverted = False

        while attempts <= self.max_retries:
            attempts += 1
            apply_edit(path, new_content, retry_context)
            ok, message = self._run_checks(path)
            if ok:
                return GuardResult(applied=True, attempts=attempts, reverted=reverted)
            failures.append(message)
            retry_context = "\n".join(failures)
            if revert is not None:
                revert()
                reverted = True

        return GuardResult(
            applied=False,
            attempts=attempts,
            reverted=reverted,
            failures=failures,
        )

    def _run_checks(self, path: Path) -> tuple[bool, str]:
        for check in self.checks:
            ok, message = check(path)
            if not ok:
                return False, message
        return True, ""
