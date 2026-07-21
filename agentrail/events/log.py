"""Append-only JSONL event timeline (OpenTelemetry GenAI-style spans).

Authoritative stream at ``.agentrail/events/events.jsonl``. Events are IMMUTABLE:
corrections are new events, never edits to old lines. One JSON object per line,
each carrying a schema ``version`` so old traces stay replayable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentrail.config import config_dir
from agentrail.models import Mode

SCHEMA_VERSION = 1
EVENTS_DIRNAME = "events"
EVENTS_FILENAME = "events.jsonl"


def new_id() -> str:
    """Return a fresh 32-char hex id for a trace or span."""

    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """A single timeline entry. Required fields mirror the events spec."""

    model_config = ConfigDict(extra="forbid")

    version: int = SCHEMA_VERSION
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    type: str
    workflow_id: str
    stage_id: str | None = None
    mode: Mode | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def events_path(root: Path) -> Path:
    return config_dir(root) / EVENTS_DIRNAME / EVENTS_FILENAME


class EventLog:
    """Thin append-only writer/reader over ``events.jsonl``.

    Writes are whole-line and atomic (one ``write`` per event) so the file is
    always valid JSONL even under concurrent control-plane writers.
    """

    def __init__(self, root: Path) -> None:
        self._path = events_path(root)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: Event) -> Event:
        """Serialize and append one event as a single line."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json() + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return event

    def emit(
        self,
        *,
        type: str,
        workflow_id: str,
        trace_id: str,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        stage_id: str | None = None,
        mode: Mode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Event:
        """Build and append an event in one call; returns the stored event."""

        event = Event(
            type=type,
            workflow_id=workflow_id,
            trace_id=trace_id,
            span_id=span_id or new_id(),
            parent_span_id=parent_span_id,
            stage_id=stage_id,
            mode=mode,
            attributes=attributes or {},
        )
        return self.append(event)

    def read(self) -> list[Event]:
        """Read all events back in order (skips blank lines)."""

        if not self._path.exists():
            return []
        events: list[Event] = []
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line:
                events.append(Event.model_validate_json(line))
        return events
