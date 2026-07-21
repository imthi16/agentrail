"""AgentRail policies subsystem — capability modes + Intent Lock enforcement."""

from __future__ import annotations

from agentrail.policies.engine import (
    Action,
    ActionType,
    Decision,
    LockView,
    PolicyEngine,
    PolicyResult,
    decide,
    verify_lock_hash,
)
from agentrail.policies.gate import (
    ApprovalCallback,
    GateOutcome,
    PolicyGate,
    deny_all_approvals,
)
from agentrail.policies.locks import (
    bind_lock_to_stage,
    lock_path,
    read_lock,
    verify_stage_lock,
    verify_workflow_locks,
    write_lock,
)

__all__ = [
    "Action",
    "ActionType",
    "ApprovalCallback",
    "Decision",
    "GateOutcome",
    "LockView",
    "PolicyEngine",
    "PolicyGate",
    "PolicyResult",
    "bind_lock_to_stage",
    "decide",
    "deny_all_approvals",
    "lock_path",
    "read_lock",
    "verify_lock_hash",
    "verify_stage_lock",
    "verify_workflow_locks",
    "write_lock",
]
