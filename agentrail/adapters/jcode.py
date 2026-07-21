"""jcode adapter — the primary/default harness (Rust, MIT, github.com/1jehuang/jcode).

Verified invocation shapes (adapters/CLAUDE.md):
- Non-interactive: ``jcode run "<prompt>"``
- Resume by memorable name: ``jcode --resume <name>``
- Persistent server: ``jcode serve`` + ``jcode connect``
- Providers: ``jcode login --provider <claude|openai|gemini|...>``

jcode has native swarm + semantic-vector memory, but AgentRail STILL runs each
stage in an isolated worktree and enforces modes at the interception layer.
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

BINARY = "jcode"


@dataclass
class JcodeAdapter:
    """Default adapter. Runs jcode as a gated subprocess."""

    name: str = "jcode"
    runner: Runner = field(default=default_runner)

    def available(self) -> bool:
        return harness_available(BINARY)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            session_resume=True,
            swarm=True,
            non_interactive=True,
            providers=("claude", "openai", "gemini"),
        )

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        # jcode runs non-interactively with `jcode run "<prompt>"`. Model routing
        # is applied by the profile layer; mode is enforced by the policies gate,
        # not by jcode flags, so it is not injected into the argv here.
        return [BINARY, "run", prompt]

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
        handle = SessionHandle(adapter=self.name, name="jcode-run", cwd=cwd, argv=argv)
        if dry_run or not self.available():
            handle.started = False
            return handle
        completed = self.runner(argv, cwd)
        handle.started = completed.returncode == 0
        handle.output = completed.stdout
        return handle

    def resume_argv(self, session_name: str) -> list[str]:
        return [BINARY, "--resume", session_name]

    def send(self, handle: SessionHandle, text: str) -> None:
        # Non-interactive jcode run does not accept mid-session input; a
        # persistent `jcode serve`/`connect` transport is a v0.3 concern.
        raise NotImplementedError("jcode `run` is non-interactive; use serve/connect (v0.3)")

    def capture(self, handle: SessionHandle) -> str:
        return handle.output

    def stop(self, handle: SessionHandle) -> None:
        handle.started = False
