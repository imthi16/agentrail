"""Subprocess-interception gate — the real enforcement point.

Every shell command and file edit the control plane performs on behalf of a
harness passes through :class:`PolicyGate` BEFORE it happens. The gate:

1. Builds an :class:`~agentrail.policies.engine.Action`.
2. Asks the pure decision function for ALLOW / ASK / DENY.
3. On ASK, consults an injected approval callback (default: deny, i.e. fail
   closed when running non-interactively).
4. Logs the decision to the event timeline with the rule that fired.
5. Only then performs the side effect (subprocess run / file write).

Enforcement is in code here, never in prose. The approval callback and the
subprocess runner are injected so the gate stays unit-testable with no real
processes.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentrail.events import EventLog, new_id
from agentrail.models import IntentLock, Mode
from agentrail.policies.engine import (
    Action,
    ActionType,
    Decision,
    LockView,
    PolicyResult,
    decide,
    verify_lock_hash,
)

# (action, policy_result) -> approved?
ApprovalCallback = Callable[[Action, PolicyResult], bool]


def deny_all_approvals(action: Action, result: PolicyResult) -> bool:
    """Default ASK handler: fail closed (deny) when running unattended."""

    return False


@dataclass(frozen=True)
class GateOutcome:
    """Result of passing an action through the gate."""

    action: Action
    decision: Decision
    reason: str
    performed: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW or (self.decision is Decision.ASK and self.performed)


class PolicyGate:
    """Stateful gate binding a mode + Intent Lock to a workspace root.

    Fails closed on Intent Lock hash mismatch (re-verified per call) and on any
    ASK the approver rejects.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        mode: Mode,
        workflow_id: str,
        event_log: EventLog,
        intent_lock: IntentLock | None = None,
        recorded_hash: str | None = None,
        stage_id: str | None = None,
        trace_id: str | None = None,
        approver: ApprovalCallback = deny_all_approvals,
        runner: Callable[[str, Path], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._root = workspace_root.resolve()
        self._mode = mode
        self._workflow_id = workflow_id
        self._log = event_log
        self._lock = intent_lock
        self._recorded_hash = recorded_hash
        self._stage_id = stage_id
        self._trace_id = trace_id or new_id()
        self._approver = approver
        self._runner = runner or self._default_runner
        self._view: LockView = LockView.build(self._root, intent_lock)

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def _default_runner(self, command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S602 - shell needed; gated above
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def _evaluate(self, action: Action) -> PolicyResult:
        if self._lock is not None and self._recorded_hash is not None:
            if not verify_lock_hash(self._lock, self._recorded_hash):
                return PolicyResult(Decision.DENY, "Intent Lock hash mismatch (tampered)")
        return decide(self._mode, self._view, action)

    def _log_decision(self, action: Action, result: PolicyResult, *, approved: bool) -> None:
        self._log.emit(
            type="policy.decision",
            workflow_id=self._workflow_id,
            trace_id=self._trace_id,
            stage_id=self._stage_id,
            mode=self._mode,
            attributes={
                "action_type": action.type.value,
                "path": action.path,
                "command": action.command,
                "decision": result.decision.value,
                "rule": result.reason,
                "approved": approved,
            },
        )

    def check_shell(self, command: str) -> GateOutcome:
        """Gate a shell command WITHOUT running it (decision only)."""

        action = Action(type=ActionType.SHELL, command=command)
        result = self._evaluate(action)
        approved = self._resolve_approval(action, result)
        self._log_decision(action, result, approved=approved)
        return GateOutcome(
            action=action,
            decision=result.decision,
            reason=result.reason,
            performed=False,
        )

    def run_shell(self, command: str) -> GateOutcome:
        """Gate then, if permitted, actually execute a shell command."""

        action = Action(type=ActionType.SHELL, command=command)
        result = self._evaluate(action)
        approved = self._resolve_approval(action, result)
        self._log_decision(action, result, approved=approved)
        if not approved:
            return GateOutcome(
                action=action,
                decision=result.decision,
                reason=result.reason,
                performed=False,
            )
        completed = self._runner(command, self._root)
        return GateOutcome(
            action=action,
            decision=result.decision,
            reason=result.reason,
            performed=True,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def write_file(self, path: str, content: str) -> GateOutcome:
        """Gate then, if permitted, write ``content`` to ``path``."""

        action = Action(type=ActionType.EDIT, path=path)
        result = self._evaluate(action)
        approved = self._resolve_approval(action, result)
        self._log_decision(action, result, approved=approved)
        if not approved:
            return GateOutcome(
                action=action,
                decision=result.decision,
                reason=result.reason,
                performed=False,
            )
        target = Path(path)
        if not target.is_absolute():
            target = self._root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return GateOutcome(
            action=action,
            decision=result.decision,
            reason=result.reason,
            performed=True,
        )

    def authorize_session(self, argv: list[str]) -> GateOutcome:
        """Authorize launching a (potentially mutating) harness session.

        This is a mode-level gate on *whether the harness may run at all* in the
        worktree; per-action path/shell scoping is enforced by ``run_shell`` /
        ``write_file`` (and, in auto, the Intent Lock) once live I/O lands.
        ``plan`` denies (read-only), ``manual`` asks, ``accept_edits``/``auto``
        allow. Auto additionally requires a verified Intent Lock. A tampered
        lock hash fails closed via ``_evaluate``'s hash check.
        """

        action = Action(type=ActionType.SHELL, command=" ".join(argv) or "<harness>")
        # Hash-tamper check first (fails closed regardless of mode).
        if self._lock is not None and self._recorded_hash is not None:
            if not verify_lock_hash(self._lock, self._recorded_hash):
                result = PolicyResult(Decision.DENY, "Intent Lock hash mismatch (tampered)")
            else:
                result = self._session_decision()
        else:
            result = self._session_decision()
        approved = self._resolve_approval(action, result)
        self._log.emit(
            type="policy.session_authorized",
            workflow_id=self._workflow_id,
            trace_id=self._trace_id,
            stage_id=self._stage_id,
            mode=self._mode,
            attributes={
                "argv": argv,
                "decision": result.decision.value,
                "rule": result.reason,
                "approved": approved,
            },
        )
        return GateOutcome(
            action=action,
            decision=result.decision,
            reason=result.reason,
            performed=approved,
        )

    def _session_decision(self) -> PolicyResult:
        if self._mode is Mode.PLAN:
            return PolicyResult(Decision.DENY, "plan mode is read-only")
        if self._mode is Mode.MANUAL:
            return PolicyResult(Decision.ASK, "manual mode gates the harness session")
        if self._mode is Mode.AUTO and self._lock is None:
            return PolicyResult(Decision.DENY, "auto mode requires an Intent Lock")
        return PolicyResult(Decision.ALLOW, f"{self._mode.value} mode permits the session")

    def _resolve_approval(self, action: Action, result: PolicyResult) -> bool:
        if result.decision is Decision.ALLOW:
            return True
        if result.decision is Decision.ASK:
            return self._approver(action, result)
        return False
