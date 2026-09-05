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

import re
import shlex
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libtmux import Server


# Exit status appended to the completion marker by run_result's `sh -c` wrapper.
_RC_PATTERN = re.compile(r"\brc=(\d+)\b")


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
class PaneResult:
    """Outcome of a supervised pane command.

    ``exit_code`` is None only when ``timed_out`` is True, or when the marker
    appeared without a parseable status — both mean "did not succeed".
    """

    handle: PaneHandle
    output: list[str]
    exit_code: int | None
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


@dataclass
class TmuxSupervisor:
    """Thin wrapper over libtmux for per-stage supervised panes."""

    # Isolate from the developer's default tmux server when set.
    socket_name: str | None = None
    _server: Server | None = field(default=None)

    def __post_init__(self) -> None:
        if not tmux_available():
            raise TmuxUnavailableError("tmux binary or libtmux not available")
        if self._server is None:
            import libtmux

            self._server = (
                libtmux.Server(socket_name=self.socket_name)
                if self.socket_name
                else libtmux.Server()
            )

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
        session = self.server.new_session(name, kill_session=False, attach=False)
        # Panes in AgentRail sessions get a plain POSIX shell rather than the
        # user's login shell. A heavyweight interactive shell (prompt frameworks,
        # conda init) can take seconds to start reading stdin, and keys sent
        # before then are silently dropped — which looks exactly like a hung
        # harness. `sh` is ready immediately and is what run_result targets anyway.
        _set_session_option(session, "default-shell", _posix_shell())
        return session

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

        result = self.run_result(
            stage_id,
            command,
            window_name=window_name,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        if result.timed_out:
            raise TimeoutError(f"command did not complete within {timeout}s: {command!r}")
        return result.handle, result.output

    def run_result(
        self,
        stage_id: str,
        command: str,
        *,
        window_name: str = "run",
        cwd: Path | None = None,
        timeout: float = 30.0,
        poll_interval: float = 0.2,
        history: bool = True,
    ) -> PaneResult:
        """Run ``command`` in a supervised pane and report its exit code.

        Unlike :meth:`run`, a timeout is *returned* (``timed_out=True``) rather
        than raised: the supervisor stays mechanism, and the caller decides
        policy. That matters because a timed-out harness may still be writing
        into the worktree, so the caller must not blindly roll back or kill it.
        """

        window = self.new_window(stage_id, window_name)
        pane = window.active_pane
        assert pane is not None
        sentinel = f"AGENTRAIL_DONE_{uuid.uuid4().hex}"
        # The script goes into a FILE and we type only `sh <path>`. Sending a
        # long quoted script as keystrokes into an interactive shell is fragile:
        # it wraps, and line editors with heavy prompts can mangle it. A short
        # typed line also means the sentinel never appears in the echoed command,
        # so polling cannot match the command line instead of real output.
        # `sh` keeps `$?` working regardless of the pane's login shell (fish has
        # no `$?`), and the subshell stops a command calling `exit` from skipping
        # the status line (which would look identical to a timeout).
        inner = f"cd {shlex.quote(str(cwd))} && {command}" if cwd is not None else command
        script = f"( {inner} )\n__ar_rc=$?\necho {sentinel} rc=$__ar_rc\n"
        # NB: the filename must NOT contain the sentinel — the typed `sh <path>`
        # line is echoed into the pane, and polling would match it before the
        # command had run at all.
        script_path = Path(tempfile.gettempdir()) / f"agentrail-run-{uuid.uuid4().hex}.sh"
        script_path.write_text(script, encoding="utf-8")
        pane.send_keys(f"sh {shlex.quote(str(script_path))}", enter=True)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = _capture(pane, history=history)
            marker = next((ln for ln in lines if sentinel in ln), None)
            if marker is not None:
                match = _RC_PATTERN.search(marker)
                # Marker present but no parseable rc: report None so the caller
                # treats it as a failure. Never silently assume success.
                exit_code = int(match.group(1)) if match else None
                script_path.unlink(missing_ok=True)
                return PaneResult(
                    handle=_handle_from(pane),
                    output=[ln for ln in lines if sentinel not in ln],
                    exit_code=exit_code,
                )
            time.sleep(poll_interval)

        # Left on disk deliberately: the pane may still be executing it.
        return PaneResult(
            handle=_handle_from(pane),
            output=_capture(pane, history=history),
            exit_code=None,
            timed_out=True,
        )

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


def _capture(pane: Any, *, history: bool) -> list[str]:
    """Capture the pane buffer, including scrollback when asked.

    Without ``-S -`` long harness output is truncated to the visible pane. Note
    tmux still caps this at the server's ``history-limit``.
    """

    if history:
        try:
            return _as_lines(pane.cmd("capture-pane", "-p", "-S", "-").stdout)
        except Exception:  # noqa: BLE001 - fall back to the plain capture
            pass
    return _as_lines(pane.capture_pane())


def _as_lines(captured: Any) -> list[str]:
    if isinstance(captured, list):
        return [str(line) for line in captured]
    return str(captured).splitlines()


def _posix_shell() -> str:
    """A fast, dependency-free shell for supervised panes."""

    return shutil.which("sh") or "/bin/sh"


def _set_session_option(session: Any, option: str, value: str) -> None:
    """Set a session option across libtmux versions (best-effort)."""

    try:
        if hasattr(session, "set_option"):
            session.set_option(option, value)
    except Exception:  # noqa: BLE001 - an unsupported option must not break the run
        pass


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
