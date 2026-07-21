"""Uniform harness adapter interface.

The control plane treats every harness (jcode, Claude Code, Codex, Gemini CLI)
as an interchangeable subprocess. Adapters normalize each harness's diverse
config onto AgentRail's single policy + profile model. **jcode is the default.**

Every harness still runs under the policies gate and (for long processes) inside
the tmux supervisor, regardless of the harness's own permission features.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agentrail.models import Mode

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - controlled args, no shell
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass
class SessionHandle:
    """An opaque reference to a started harness session."""

    adapter: str
    name: str
    cwd: Path
    argv: list[str] = field(default_factory=list)
    started: bool = False
    output: str = ""


@dataclass
class Capabilities:
    """What a harness can do (declared by each adapter)."""

    session_resume: bool = False
    swarm: bool = False
    non_interactive: bool = False
    providers: tuple[str, ...] = ()


class HarnessAdapter(Protocol):
    """The interface every adapter implements."""

    name: str

    def available(self) -> bool:
        """True if the harness binary is installed."""

    def capabilities(self) -> Capabilities:
        """Declare resume/swarm/providers/non-interactive support."""

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        """Return the argv this adapter would run for a prompt (no side effects)."""

    def start(
        self,
        prompt: str,
        *,
        mode: Mode,
        model_id: str | None,
        cwd: Path,
        dry_run: bool = False,
    ) -> SessionHandle:
        """Start (or simulate) a harness session for ``prompt``."""

    def send(self, handle: SessionHandle, text: str) -> None:
        """Feed input to a running session."""

    def capture(self, handle: SessionHandle) -> str:
        """Read current output."""

    def stop(self, handle: SessionHandle) -> None:
        """Terminate cleanly."""


def harness_available(binary: str) -> bool:
    return shutil.which(binary) is not None
