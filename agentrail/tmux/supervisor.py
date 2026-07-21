"""Tmux supervisor (libtmux) — persistent panes for long-running processes.

Runs compiles, test suites, and dev servers in tmux so they survive control
plane crashes and stay attachable. libtmux is pre-1.0 and pinned narrowly; the
object model is ``Server -> Session -> Window -> Pane``.

Key behaviours (tmux/CLAUDE.md):
- Sessions created with ``attach=False`` (the control plane never attaches).
- Deterministic completion via a unique marker echoed after the command, polled
  from ``capture_pane`` output.
- ``remain-on-exit on`` so a finished/failed pane stays inspectable.
- ``synchronize-panes`` left OFF (never broadcast keystrokes across stages).

If tmux/libtmux is unavailable, :func:`tmux_available` returns False and callers
degrade gracefully instead of crashing the build.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libtmux import Server


class TmuxUnavailableError(RuntimeError):
    """Raised when a tmux operation is attempted without tmux/libtmux."""


def tmux_available() -> bool:
    """True if both the tmux binary and libtmux import are present."""

    if shutil.which("tmux") is None:
        return False
    try:
        import libtmux  # noqa: F401
    except ImportError:
        return False
    return True


def session_name(stage_id: str) -> str:
    return f"agentrail-{stage_id}"


@dataclass
class PaneHandle:
    """Recorded identifiers for a supervised pane (stored in checkpoints)."""

    session_id: str
    window_id: str
    pane_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "window_id": self.window_id,
            "pane_id": self.pane_id,
        }


@dataclass
class TmuxSupervisor:
    """Thin wrapper over libtmux for per-stage supervised panes."""

    _server: Server | None = field(default=None)

    def __post_init__(self) -> None:
        if not tmux_available():
            raise TmuxUnavailableError("tmux binary or libtmux not available")
        if self._server is None:
            import libtmux

            self._server = libtmux.Server()

    @property
    def server(self) -> Server:
        assert self._server is not None
        return self._server

    def ensure_session(self, stage_id: str) -> Any:
        """Return the stage session, creating it (detached) if needed."""

        name = session_name(stage_id)
        existing = self.server.sessions.filter(session_name=name)
        if existing:
            return existing[0]
        return self.server.new_session(name, kill_session=False, attach=False)

    def new_window(self, stage_id: str, window_name: str) -> Any:
        session = self.ensure_session(stage_id)
        window = session.new_window(window_name=window_name, attach=False)
        # Keep finished/failed panes inspectable; never broadcast keystrokes.
        _set_window_option(window, "remain-on-exit", "on")
        _set_window_option(window, "synchronize-panes", "off")
        return window

    def run(
        self,
        stage_id: str,
        command: str,
        *,
        window_name: str = "run",
        timeout: float = 30.0,
        poll_interval: float = 0.2,
    ) -> tuple[PaneHandle, list[str]]:
        """Run ``command`` in a supervised pane; block until a marker appears.

        Returns the recorded :class:`PaneHandle` and the captured pane buffer.
        Raises :class:`TimeoutError` if the marker never appears.
        """

        window = self.new_window(stage_id, window_name)
        pane = window.active_pane
        assert pane is not None
        # Split the sentinel via shell quote-concatenation so the CONTIGUOUS
        # marker only appears in real output, never in the echoed command line
        # (avoids a race where polling matches the typed command).
        sentinel = f"AGENTRAIL_DONE_{uuid.uuid4().hex}"
        half = len(sentinel) // 2
        emit = f"echo '{sentinel[:half]}''{sentinel[half:]}'"
        pane.send_keys(f"{command}; {emit}", enter=True)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = _as_lines(pane.capture_pane())
            if any(sentinel in line for line in lines):
                handle = _handle_from(pane)
                return handle, [ln for ln in lines if sentinel not in ln]
            time.sleep(poll_interval)

        raise TimeoutError(f"command did not complete within {timeout}s: {command!r}")

    def capture(self, stage_id: str, window_name: str = "run") -> list[str]:
        session = self.ensure_session(stage_id)
        windows = session.windows.filter(window_name=window_name)
        if not windows:
            return []
        pane = windows[0].active_pane
        return _as_lines(pane.capture_pane()) if pane is not None else []

    def kill(self, stage_id: str) -> None:
        """Kill the stage's session if it exists (idempotent)."""

        name = session_name(stage_id)
        for session in self.server.sessions.filter(session_name=name):
            session.kill()


def _as_lines(captured: Any) -> list[str]:
    if isinstance(captured, list):
        return [str(line) for line in captured]
    return str(captured).splitlines()


def _set_window_option(window: Any, option: str, value: str) -> None:
    """Set a window option across libtmux versions (0.60 renamed the method)."""

    if hasattr(window, "set_option"):
        window.set_option(option, value)
    else:
        window.set_window_option(option, value)


def _handle_from(pane: Any) -> PaneHandle:
    return PaneHandle(
        session_id=str(getattr(pane, "session_id", "")),
        window_id=str(getattr(pane, "window_id", "")),
        pane_id=str(getattr(pane, "pane_id", "")),
    )
