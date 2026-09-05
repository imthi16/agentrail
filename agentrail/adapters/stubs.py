"""Stub adapters — Claude Code, Codex, Gemini CLI (interface parity, v0.3 fill-in).

Each declares its binary + capabilities and can build a non-interactive argv, so
the control plane can route to them uniformly. Live session wiring lands in v0.3;
mode enforcement always stays in the policies gate, never the harness flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentrail.adapters.base import (
    Capabilities,
    Runner,
    SessionHandle,
    default_runner,
    harness_available,
)
from agentrail.models import Mode


@dataclass
class _BaseStub:
    name: str
    binary: str
    runner: Runner = field(default=default_runner)

    def available(self) -> bool:
        return harness_available(self.binary)

    def send(self, handle: SessionHandle, text: str) -> None:
        raise NotImplementedError(f"{self.name} live sessions are a v0.3 feature")

    def capture(self, handle: SessionHandle) -> str:
        return handle.output

    def stop(self, handle: SessionHandle) -> None:
        handle.started = False

    def start(
        self,
        prompt: str,
        *,
        mode: Mode,
        model_id: str | None,
        cwd: Path,
        dry_run: bool = False,
    ) -> SessionHandle:
        argv = self.build_argv(prompt, mode=mode, model_id=model_id)
        return SessionHandle(adapter=self.name, name=f"{self.name}-run", cwd=cwd, argv=argv)

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:  # noqa: ARG002
        return [self.binary, prompt]


@dataclass
class ClaudeCodeAdapter(_BaseStub):
    name: str = "claude-code"
    binary: str = "claude"

    def capabilities(self) -> Capabilities:
        # Claude Code has native plan/acceptEdits/auto modes + an Effort selector;
        # AgentRail maps its modes/profiles on but keeps its own enforcement.
        return Capabilities(
            session_resume=True,
            swarm=False,
            non_interactive=True,
            providers=("claude",),
            process_backed=True,
            # No model flag verified against a pinned harness version; see
            # adapters/CLAUDE.md — we do not ship flags we have not verified.
            model_selection=False,
            edits_files=True,
        )

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:  # noqa: ARG002
        return [self.binary, "-p", prompt]


@dataclass
class CodexAdapter(_BaseStub):
    name: str = "codex"
    binary: str = "codex"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            session_resume=True,
            swarm=False,
            non_interactive=True,
            providers=("openai",),
            process_backed=True,
            # No model flag verified against a pinned harness version; see
            # adapters/CLAUDE.md — we do not ship flags we have not verified.
            model_selection=False,
            edits_files=True,
        )

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:  # noqa: ARG002
        return [self.binary, "exec", prompt]


@dataclass
class GeminiAdapter(_BaseStub):
    name: str = "gemini"
    binary: str = "gemini"

    def capabilities(self) -> Capabilities:
        # Gemini CLI defaults to a read-only Plan Mode; AgentRail maps `plan` on
        # and uses thinking_level from profiles.
        return Capabilities(
            session_resume=False,
            swarm=False,
            non_interactive=True,
            providers=("gemini",),
            process_backed=True,
            # No model flag verified against a pinned harness version; see
            # adapters/CLAUDE.md — we do not ship flags we have not verified.
            model_selection=False,
            edits_files=True,
        )

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:  # noqa: ARG002
        return [self.binary, "-p", prompt]
