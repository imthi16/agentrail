"""OpenCode API adapter — OpenAI-compatible HTTP endpoint (Go or Zen).

Unlike the subprocess harnesses this posts the prompt to the configured
``base_url`` (default Go: https://opencode.ai/go/v1; Zen users flip one string in
`.agentrail/config.yaml`). The API key comes from the env var named by
``api_key_env`` (default OPENCODE_API_KEY); it is never written to YAML or to the
event timeline.

Mode enforcement stays with the policies gate (adapters/CLAUDE.md): this adapter
declares capability only — tier routing picks model_id upstream of the call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from httpx import Client, HTTPError

from agentrail.adapters.base import Capabilities, SessionHandle
from agentrail.config import OpencodeSettings
from agentrail.models import Mode

ClientFactory = Callable[..., Client]


@dataclass
class OpencodeApiAdapter:
    """OpenCode Go/Zen chat-completions endpoint adapter."""

    name: str = "opencode"
    settings: OpencodeSettings = field(default_factory=OpencodeSettings)
    # Injectable for offline tests (httpx.MockTransport etc.).
    client_factory: ClientFactory = field(default=Client)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            session_resume=False,
            swarm=False,
            non_interactive=True,
            providers=("opencode",),
            # An HTTP chat call, not a process: never supervise it in a pane.
            process_backed=False,
            # The routed model really is sent, in the POST body.
            model_selection=True,
            # Returns text; it does not edit the worktree, so an empty worktree
            # after this adapter runs is expected, not a failure.
            edits_files=False,
        )

    def import_key(self) -> str | None:
        """Resolve the API key from the configured env var. None if unset."""

        import os

        return os.environ.get(self.settings.api_key_env)

    def available(self) -> bool:
        return self.import_key() is not None

    def build_argv(self, prompt: str, *, mode: Mode, model_id: str | None) -> list[str]:
        # Mirrors the request that start() would POST, as a readable argv-like
        # form for dry-run/status echoes. mode is enforced by the policies gate;
        # only model_id is injected.
        return [f"POST {self.base_url}/chat/completions", f"model={model_id or '?'}"]

    @property
    def base_url(self) -> str:
        return self.settings.base_url.rstrip("/")

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
        handle = SessionHandle(adapter=self.name, name="opencode-run", cwd=cwd, argv=argv)
        key = self.import_key()
        if dry_run or key is None:
            handle.started = False
            handle.output = "dry-run" if dry_run else f"{self.settings.api_key_env} not set"
            return handle
        if model_id is None:
            handle.started = False
            handle.output = "no routed model_id"
            return handle
        try:
            with self.client_factory(
                base_url=self.base_url, headers={"Authorization": f"Bearer {key}"}
            ) as http:
                response = http.post(
                    "/chat/completions",
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                body = response.json()
                choice = body["choices"][0]["message"]["content"]
                handle.started = True
                handle.output = choice if isinstance(choice, str) else repr(choice)
        except (HTTPError, KeyError, ValueError) as exc:
            handle.started = False
            handle.output = str(exc)
        return handle

    def send(self, handle: SessionHandle, text: str) -> None:
        raise NotImplementedError("opencode API is single-shot; sessions land in v0.3")

    def capture(self, handle: SessionHandle) -> str:
        return handle.output

    def stop(self, handle: SessionHandle) -> None:
        handle.started = False
