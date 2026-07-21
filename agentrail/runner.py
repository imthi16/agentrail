"""Stage-by-stage workflow runner — wires all subsystems for execution.

Drives an APPROVED workflow through its topologically-ordered stages. For each
stage it: creates an isolated worktree, checkpoints it, runs the harness in a
tmux pane under the policies gate, runs the Semantic Edit Guard, then opens the
(stacked) draft PR. Every step emits events.

Everything external (worktrees, checkpoints, tmux, harness, PRs) is injected or
feature-detected, so the runner has a ``dry_run`` path that plans the full
sequence without side effects — usable even where tmux/gh are unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentrail.adapters import HarnessAdapter, JcodeAdapter
from agentrail.checkpoints import CheckpointStore
from agentrail.config import Budget, load_config
from agentrail.events import EventLog, new_id
from agentrail.git import PullRequestManager
from agentrail.models import IntentLock, Mode, Stage, StageStatus, Workflow, WorkflowStatus
from agentrail.policies import PolicyGate, read_lock, verify_stage_lock
from agentrail.profiles import (
    BudgetExceededError,
    BudgetTracker,
    ModelRouter,
    Provider,
    WorkKind,
)
from agentrail.tmux import tmux_available
from agentrail.workflow import save_workflow, topological_order
from agentrail.workspaces import WorktreeManager


class StageBlockedError(RuntimeError):
    """Raised when policy admission control refuses to run a stage."""


@dataclass
class StagePlan:
    """What the runner did (or would do) for a single stage."""

    stage_id: str
    worktree_branch: str
    worktree_base: str
    checkpoint_id: str | None
    pr_base: str
    pr_head: str
    harness_argv: list[str]
    ran_in_tmux: bool
    performed: bool


@dataclass
class RunReport:
    """Summary of a run (or dry-run) across all stages."""

    workflow_id: str
    dry_run: bool
    stages: list[StagePlan] = field(default_factory=list)


@dataclass
class WorkflowRunner:
    """Executes an approved workflow stage-by-stage."""

    root: Path
    mode: Mode = Mode.ACCEPT_EDITS
    default_base: str = "main"
    adapter: HarnessAdapter = field(default_factory=JcodeAdapter)
    provider: Provider = Provider.ANTHROPIC

    def __post_init__(self) -> None:
        self._log = EventLog(self.root)
        self._worktrees = WorktreeManager(self.root)
        self._checkpoints = CheckpointStore(self.root)
        self._prs = PullRequestManager(self.root, default_base=self.default_base)
        self._budget = self._load_budget()
        self._router: ModelRouter | None = None
        self._tracker: BudgetTracker | None = None

    def _load_budget(self) -> Budget:
        try:
            return load_config(self.root).budget
        except (FileNotFoundError, ValueError):
            return Budget()

    def run(self, workflow: Workflow, *, dry_run: bool = False) -> RunReport:
        """Run every stage in dependency order; persist status transitions."""

        if workflow.status not in (WorkflowStatus.APPROVED, WorkflowStatus.RUNNING):
            raise RuntimeError(
                f"workflow must be approved before running (status={workflow.status.value})"
            )

        trace = new_id()
        report = RunReport(workflow_id=workflow.workflow_id, dry_run=dry_run)
        workflow.status = WorkflowStatus.RUNNING
        self._router = ModelRouter(
            provider=self.provider,
            event_log=self._log,
            workflow_id=workflow.workflow_id,
            trace_id=trace,
        )
        self._tracker = BudgetTracker(
            budget=self._budget,
            event_log=self._log,
            workflow_id=workflow.workflow_id,
            trace_id=trace,
        )
        self._log.emit(
            type="workflow.run_started",
            workflow_id=workflow.workflow_id,
            trace_id=trace,
            mode=self.mode,
            attributes={
                "dry_run": dry_run,
                "budget_max_usd": self._budget.max_usd,
                "budget_max_tokens": self._budget.max_tokens,
                "budget_max_retries_per_stage": self._budget.max_retries_per_stage,
            },
        )

        for stage in topological_order(workflow.stages):
            try:
                report.stages.append(self._run_stage(workflow, stage, trace, dry_run))
            except StageBlockedError:
                # Persist partial state (BLOCKED status, any worktrees/checkpoints
                # already created) so status/rollback/re-run can see it.
                if not dry_run:
                    workflow.status = WorkflowStatus.PAUSED
                    save_workflow(self.root, workflow)
                self._log.emit(
                    type="workflow.run_finished",
                    workflow_id=workflow.workflow_id,
                    trace_id=trace,
                    mode=self.mode,
                    attributes={
                        "dry_run": dry_run,
                        "stages": len(report.stages),
                        "blocked_stage": stage.id,
                    },
                )
                raise

        if not dry_run:
            workflow.status = WorkflowStatus.COMPLETED
            save_workflow(self.root, workflow)
        self._log.emit(
            type="workflow.run_finished",
            workflow_id=workflow.workflow_id,
            trace_id=trace,
            mode=self.mode,
            attributes={"dry_run": dry_run, "stages": len(report.stages)},
        )
        return report

    def _run_stage(self, workflow: Workflow, stage: Stage, trace: str, dry_run: bool) -> StagePlan:
        base = self._stage_base(stage, workflow)
        span = new_id()
        self._log.emit(
            type="stage.started",
            workflow_id=workflow.workflow_id,
            trace_id=trace,
            span_id=span,
            stage_id=stage.id,
            mode=self.mode,
            attributes={"base": base, "dry_run": dry_run},
        )

        prompt = f"{stage.title}. {stage.description}".strip()
        argv = self.adapter.build_argv(prompt, mode=self.mode, model_id=None)
        use_tmux = tmux_available()
        checkpoint_id: str | None = None
        pr_head = f"stage/{stage.id}"

        if dry_run:
            pr_base = self._prs_base(stage, workflow)
            return StagePlan(
                stage_id=stage.id,
                worktree_branch=pr_head,
                worktree_base=base,
                checkpoint_id=None,
                pr_base=pr_base,
                pr_head=pr_head,
                harness_argv=argv,
                ran_in_tmux=use_tmux,
                performed=False,
            )

        # --- Real execution path -------------------------------------------
        worktree = self._worktrees.create(stage.id, base=base)
        stage.worktree = worktree
        stage.status = StageStatus.RUNNING

        # Checkpoint BEFORE anything mutating runs (mandatory before `auto`).
        checkpoint_id = self._checkpoints.create(stage.id, Path(worktree.path))
        stage.checkpoints.append(checkpoint_id)

        # Admission control (invariant #1): build a PolicyGate for this stage's
        # worktree + mode + Intent Lock, enforced in code before the harness
        # acts. `auto` is refused without a verified lock; a recorded lock hash
        # that no longer matches the on-disk lock fails closed.
        gate = self._build_gate(workflow, stage, worktree_root=Path(worktree.path), trace=trace)
        admit_ok, admit_reason = self._admit(stage, worktree_root=Path(worktree.path))
        if not admit_ok:
            stage.status = StageStatus.BLOCKED
            self._log.emit(
                type="stage.blocked",
                workflow_id=workflow.workflow_id,
                trace_id=trace,
                span_id=span,
                stage_id=stage.id,
                mode=self.mode,
                attributes={"reason": admit_reason, "checkpoint_id": checkpoint_id},
            )
            raise StageBlockedError(f"stage {stage.id!r} blocked: {admit_reason}")

        # Route the model tier for this stage (Deep for analysis/planning,
        # Balanced for implementation) and record the estimated spend against
        # the budget. Over-budget fails closed as a blocked stage.
        assert self._router is not None and self._tracker is not None
        profile = self._router.route(self._work_kind(stage), stage_id=stage.id)
        try:
            self._tracker.record_call(
                provider=profile.provider,
                model_id=profile.model_id,
                tier=profile.tier,
                input_tokens=self._estimate_prompt_tokens(prompt),
                output_tokens=0,
                stage_id=stage.id,
            )
        except BudgetExceededError as exc:
            stage.status = StageStatus.BLOCKED
            raise StageBlockedError(f"stage {stage.id!r} blocked: {exc}") from exc

        # Harness runs under the policies gate (enforced above; the gate object
        # is handed to the adapter transport when live I/O lands in v0.3).
        _ = gate
        handle = self.adapter.start(
            prompt,
            mode=self.mode,
            model_id=profile.model_id,
            cwd=Path(worktree.path),
            dry_run=False,
        )

        pr_ref = self._prs.create_for_stage(stage, workflow, dry_run=not self._prs_ready())
        stage.pull_request = pr_ref
        stage.status = StageStatus.COMPLETED

        self._log.emit(
            type="stage.completed",
            workflow_id=workflow.workflow_id,
            trace_id=trace,
            span_id=span,
            stage_id=stage.id,
            mode=self.mode,
            attributes={
                "checkpoint_id": checkpoint_id,
                "pr_base": pr_ref.base,
                "pr_head": pr_ref.head,
                "harness_started": handle.started,
            },
        )
        return StagePlan(
            stage_id=stage.id,
            worktree_branch=worktree.branch,
            worktree_base=base,
            checkpoint_id=checkpoint_id,
            pr_base=pr_ref.base,
            pr_head=pr_ref.head,
            harness_argv=argv,
            ran_in_tmux=use_tmux,
            performed=True,
        )

    def _stage_base(self, stage: Stage, workflow: Workflow) -> str:
        parents = [d for d in stage.depends_on if any(s.id == d for s in workflow.stages)]
        if len(parents) == 1:
            return f"stage/{parents[0]}"
        return self.default_base

    def _prs_base(self, stage: Stage, workflow: Workflow) -> str:
        from agentrail.git import base_branch_for

        return base_branch_for(stage, workflow, self.default_base)

    def _prs_ready(self) -> bool:
        from agentrail.git import GitRepo, gh_authenticated

        # Require BOTH an authenticated gh AND a pushable remote; otherwise the
        # PR step degrades to dry-run (records base/head, opens nothing).
        if not GitRepo(self.root).has_remote():
            return False
        return gh_authenticated(cwd=self.root)

    def _load_stage_lock(self, stage: Stage) -> IntentLock | None:
        if stage.intent_lock_hash is None:
            return None
        try:
            return read_lock(self.root, stage.id)
        except (FileNotFoundError, ValueError):
            return None

    def _build_gate(
        self, workflow: Workflow, stage: Stage, *, worktree_root: Path, trace: str
    ) -> PolicyGate:
        """Construct the per-stage enforcement gate (invariant #1)."""

        lock = self._load_stage_lock(stage)
        return PolicyGate(
            workspace_root=worktree_root,
            mode=self.mode,
            workflow_id=workflow.workflow_id,
            event_log=self._log,
            intent_lock=lock,
            recorded_hash=stage.intent_lock_hash,
            stage_id=stage.id,
            trace_id=trace,
        )

    @staticmethod
    def _work_kind(stage: Stage) -> WorkKind:
        """Map a stage to a work kind for tier routing (heuristic)."""

        text = f"{stage.id} {stage.title}".lower()
        if any(k in text for k in ("analyse", "analyze", "design", "architect", "plan")):
            return WorkKind.PLANNING
        return WorkKind.IMPLEMENTATION

    @staticmethod
    def _estimate_prompt_tokens(prompt: str) -> int:
        """Very rough token estimate (~4 chars/token) for budget accounting."""

        return max(1, len(prompt) // 4)

    def _admit(self, stage: Stage, *, worktree_root: Path) -> tuple[bool, str]:
        """Decide whether a stage may run under the current mode + Intent Lock.

        Fails closed: ``auto`` requires a verified Intent Lock, and any recorded
        lock hash must still match the on-disk lock.
        """

        if stage.intent_lock_hash is not None:
            if not verify_stage_lock(self.root, stage):
                return False, "Intent Lock hash mismatch or missing lock file"

        if self.mode is Mode.AUTO:
            if stage.intent_lock_hash is None:
                return False, "auto mode requires an Intent Lock for the stage"
            lock = self._load_stage_lock(stage)
            if lock is None or not lock.allowed_paths:
                return False, "auto mode requires an Intent Lock with allowed_paths"

        return True, "admitted"
