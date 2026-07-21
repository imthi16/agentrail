"""AgentRail adapters subsystem — uniform harness interface (jcode default)."""

from __future__ import annotations

from agentrail.adapters.base import (
    Capabilities,
    HarnessAdapter,
    SessionHandle,
    harness_available,
)
from agentrail.adapters.jcode import JcodeAdapter
from agentrail.adapters.stubs import (
    ClaudeCodeAdapter,
    CodexAdapter,
    GeminiAdapter,
)

_ADAPTERS: dict[str, type] = {
    "jcode": JcodeAdapter,
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
}


def get_adapter(name: str = "jcode") -> HarnessAdapter:
    """Return an adapter instance by name (defaults to jcode)."""

    try:
        cls = _ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown harness adapter: {name}") from exc
    adapter: HarnessAdapter = cls()
    return adapter


def adapter_names() -> list[str]:
    return list(_ADAPTERS.keys())


__all__ = [
    "Capabilities",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "GeminiAdapter",
    "HarnessAdapter",
    "JcodeAdapter",
    "SessionHandle",
    "adapter_names",
    "get_adapter",
    "harness_available",
]
