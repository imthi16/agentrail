# agentrail/tmux/ — Session & Pane Supervision (libtmux)

Runs long-lived processes (compiles, test suites, dev servers) in persistent tmux
sessions so they survive control-plane crashes and stay attachable. Library:
`libtmux` — **pre-1.0; APIs change through 2026**, so it is pinned narrowly in
pyproject.toml and the object model must be re-verified on upgrade.

## Object model & verified API
`Server → Session → Window → Pane` (typed objects).
```python
import libtmux
server = libtmux.Server()                                   # optional socket_name=...
session = server.new_session("agentrail-<stage-id>", kill_session=False)
# lookup instead of create: server.sessions.get(session_name="agentrail-<id>")
window = session.new_window(window_name="tests", attach=False)
pane = window.active_pane                                    # or window.split()
pane.send_keys("pytest -q", enter=True)                     # enter=False types w/o running
lines = pane.capture_pane()                                 # -> list[str]
```
- Filter/lookup: `server.sessions.get(session_name=...)`, `.filter(...)`.
- `capture_pane()` returns a list of lines; pass `escape_sequences=True` to keep
  ANSI, `join_wrapped=...` for wrapped lines.
- `send_keys(..., suppress_history=True)` hides from shell history (default is now
  `False`). Prefer legacy-safe `window.split()` over deprecated `split_window()`.
- Raw escape hatch: `pane.cmd("capture-pane", "-p").stdout`.

## Deterministic completion tracking
Inject a unique marker and poll until it appears, to avoid races:
```python
pane.send_keys('pytest -q; echo "__AGENTRAIL_DONE_%s__"' % run_id, enter=True)
# poll capture_pane() until the marker line is present, then read the buffer
```

## Persistence & observability
- Set window option `remain-on-exit on` so a finished/failed process leaves the
  pane inspectable; use `respawn-pane` to restart.
- Use `set-hook` with `pane-died` / `pane-exited` to notify the control plane.
- Record session/window/pane IDs (`%<n>`) in the stage checkpoint so rollback and
  reattach can find/kill the right processes.

## Safety
- Keep `synchronize-panes` OFF by default — it broadcasts keystrokes to every
  pane and can trigger simultaneous destructive writes across isolated stages.

## Do / Don't
- DO create sessions with `attach=False`; the control plane never attaches
  interactively (the user runs `agentrail ... attach` themselves).
- DON'T launch long processes as bare subprocesses — always via a supervised pane.
