"""Capability-mode enforcement engine (deterministic, no LLM in the loop).

This is a **security boundary implemented in Python**. The core is a pure
decision function ``decide(mode, intent_lock, action) -> Decision`` that the
subprocess-interception layer (added later) calls BEFORE the harness acts.

Rules:
- Deny always wins over allow.
- Path checks use RESOLVED absolute paths (block ``..``/symlink escapes).
- Shell checks match command prefixes against allow/deny lists.
- Fail closed (DENY) on any ambiguity or Intent Lock hash mismatch.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agentrail.models import IntentLock, Mode


class Decision(StrEnum):
    """Outcome of a policy check."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ActionType(StrEnum):
    """The kind of action being gated."""

    READ = "read"
    EDIT = "edit"
    SHELL = "shell"


@dataclass(frozen=True)
class Action:
    """A gated action. ``path`` is set for read/edit; ``command`` for shell."""

    type: ActionType
    path: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class PolicyResult:
    """A decision plus the human-readable rule that produced it."""

    decision: Decision
    reason: str


@dataclass(frozen=True)
class LockView:
    """A verified Intent Lock scoped to a workspace root.

    ``allowed``/``denied`` hold resolved absolute path roots so checks never
    depend on the current working directory.
    """

    workspace_root: Path
    allowed: tuple[Path, ...] = ()
    denied: tuple[Path, ...] = ()
    shell_allow: tuple[str, ...] = ()
    shell_deny: tuple[str, ...] = ()

    @classmethod
    def build(cls, workspace_root: Path, lock: IntentLock | None) -> LockView:
        root = workspace_root.resolve()
        if lock is None:
            return cls(workspace_root=root)
        return cls(
            workspace_root=root,
            allowed=tuple((root / p).resolve() for p in lock.allowed_paths),
            denied=tuple((root / p).resolve() for p in lock.denied_paths),
            shell_allow=tuple(lock.shell_allow),
            shell_deny=tuple(lock.shell_deny),
        )


def _is_within(child: Path, parent: Path) -> bool:
    """True if resolved ``child`` is ``parent`` or nested beneath it."""

    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve(view: LockView, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = view.workspace_root / candidate
    return candidate.resolve()


def _check_path(view: LockView, raw_path: str | None) -> PolicyResult | None:
    """Deny-list + escape checks shared by read/edit. ``None`` means 'no denial'."""

    if raw_path is None:
        return PolicyResult(Decision.DENY, "missing path")

    resolved = _resolve(view, raw_path)

    if not _is_within(resolved, view.workspace_root):
        return PolicyResult(Decision.DENY, "path escapes workspace root")

    for denied in view.denied:
        if _is_within(resolved, denied):
            return PolicyResult(Decision.DENY, f"path in denied_paths ({denied})")

    return None


def _within_allowed(view: LockView, raw_path: str) -> bool:
    """True if there is no allow-list, or the path falls inside it."""

    if not view.allowed:
        return True
    resolved = _resolve(view, raw_path)
    return any(_is_within(resolved, allowed) for allowed in view.allowed)


def _command_prefixes(command: str, prefixes: tuple[str, ...]) -> str | None:
    """Return the matching prefix if ``command`` starts with any of them."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    joined = " ".join(tokens)
    for prefix in prefixes:
        norm = " ".join(prefix.split())
        if joined == norm or joined.startswith(norm + " "):
            return prefix
    return None


def decide(mode: Mode, lock: LockView, action: Action) -> PolicyResult:
    """Pure decision function. Never performs I/O beyond path resolution."""

    if action.type is ActionType.READ:
        denial = _check_path(lock, action.path)
        if denial is not None:
            return denial
        return PolicyResult(Decision.ALLOW, "reads permitted in all modes")

    if action.type is ActionType.EDIT:
        return _decide_edit(mode, lock, action)

    if action.type is ActionType.SHELL:
        return _decide_shell(mode, lock, action)

    return PolicyResult(Decision.DENY, "unknown action type")


def _decide_edit(mode: Mode, lock: LockView, action: Action) -> PolicyResult:
    if mode is Mode.PLAN:
        return PolicyResult(Decision.DENY, "plan mode is read-only")

    denial = _check_path(lock, action.path)
    if denial is not None:
        return denial
    assert action.path is not None  # guaranteed by _check_path

    if not _within_allowed(lock, action.path):
        return PolicyResult(Decision.DENY, "path outside allowed_paths")

    if mode is Mode.MANUAL:
        return PolicyResult(Decision.ASK, "manual mode gates every edit")
    if mode is Mode.ACCEPT_EDITS:
        return PolicyResult(Decision.ALLOW, "accept_edits auto-approves edits")
    # auto: edits allowed only strictly within the Intent Lock scope.
    if not lock.allowed:
        return PolicyResult(Decision.DENY, "auto mode requires an Intent Lock scope")
    return PolicyResult(Decision.ALLOW, "auto edit within Intent Lock")


def _decide_shell(mode: Mode, lock: LockView, action: Action) -> PolicyResult:
    command = (action.command or "").strip()
    if not command:
        return PolicyResult(Decision.DENY, "empty shell command")

    denied_prefix = _command_prefixes(command, lock.shell_deny)
    if denied_prefix is not None:
        return PolicyResult(Decision.DENY, f"command in shell_deny ({denied_prefix!r})")

    if mode is Mode.PLAN:
        return PolicyResult(Decision.DENY, "plan mode denies all shell commands")
    if mode in (Mode.MANUAL, Mode.ACCEPT_EDITS):
        return PolicyResult(Decision.ASK, f"{mode.value} mode gates shell commands")

    # auto: only allow commands explicitly permitted by the lock.
    if not lock.shell_allow:
        return PolicyResult(Decision.DENY, "auto mode requires shell_allow entries")
    if _command_prefixes(command, lock.shell_allow) is None:
        return PolicyResult(Decision.DENY, "command not in shell_allow")
    return PolicyResult(Decision.ALLOW, "auto shell within Intent Lock")


def verify_lock_hash(lock: IntentLock, recorded_hash: str) -> bool:
    """True iff the live lock still matches the hash recorded in the workflow."""

    return lock.sha256() == recorded_hash


@dataclass
class PolicyEngine:
    """Stateful wrapper that fails closed on Intent Lock hash mismatch.

    Bind a workspace root, mode, and (optionally) an Intent Lock + its recorded
    hash. ``check`` re-verifies the hash on every call so a tampered lock is
    detected and every action is denied.
    """

    workspace_root: Path
    mode: Mode
    intent_lock: IntentLock | None = None
    recorded_hash: str | None = None
    _view: LockView = field(init=False)

    def __post_init__(self) -> None:
        self._view = LockView.build(self.workspace_root, self.intent_lock)

    def check(self, action: Action) -> PolicyResult:
        if self.intent_lock is not None and self.recorded_hash is not None:
            if not verify_lock_hash(self.intent_lock, self.recorded_hash):
                return PolicyResult(Decision.DENY, "Intent Lock hash mismatch (tampered)")
        return decide(self.mode, self._view, action)
