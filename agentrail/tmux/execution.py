"""Harness execution seam — run a harness argv inside a supervised tmux pane.

This is the injectable boundary that makes invariant #4 real without making the
run loop depend on the ambient environment: the runner asks for an executor once
and either has one or does not. Tests inject a fake, so the suite behaves
identically on machines with and without tmux.

Kept separate from ``supervisor`` so the supervisor stays a thin libtmux
wrapper: this module knows about harness sessions, that one knows about panes.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentrail.adapters import SessionHandle
from agentrail.tmux.supervisor import PaneHandle, TmuxSupervisor


@dataclass
class ExecResult:
    """What a supervised harness run produced."""

    handle: SessionHandle
    pane: PaneHandle | None = None
    exit_code: int | None = None
    timed_out: bool = False


class HarnessExecutor(Protocol):
    """Runs a harness argv somewhere observable and reports the outcome."""

    def run_argv(
        self,
        *,
        stage_id: str,
        adapter_name: str,
        argv: list[str],
        cwd: Path,
        attempt: str,
    ) -> ExecResult: ...


@dataclass
class TmuxHarnessExecutor:
    """Runs the harness in a persistent, attachable tmux pane (invariant #4)."""

    supervisor: TmuxSupervisor
    timeout: float = 1800.0
    poll_interval: float = 0.5
    _window_prefix: str = field(default="run", init=False, repr=False)

    def run_argv(
        self,
        *,
        stage_id: str,
        adapter_name: str,
        argv: list[str],
        cwd: Path,
        attempt: str,
    ) -> ExecResult:
        # A unique window per attempt: a resumed stage reuses its session, and
        # a fixed name would stack windows and make capture() read the wrong one.
        window_name = f"{self._window_prefix}-{attempt[:8]}"
        result = self.supervisor.run_result(
            stage_id,
            shlex.join(argv),
            window_name=window_name,
            cwd=cwd,
            timeout=self.timeout,
            poll_interval=self.poll_interval,
        )
        handle = SessionHandle(
            adapter=adapter_name,
            name=f"{adapter_name}-{stage_id}",
            cwd=cwd,
            argv=argv,
            started=result.succeeded,
            output="\n".join(result.output),
        )
        return ExecResult(
            handle=handle,
            pane=result.handle,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
