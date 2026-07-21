"""Event log: JSONL append/read round-trip, immutability, required fields."""

from __future__ import annotations

import json
from pathlib import Path

from agentrail.events import Event, EventLog, events_path, new_id
from agentrail.models import Mode


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    trace = new_id()

    log.emit(type="workflow.planned", workflow_id="wf-1", trace_id=trace)
    log.emit(
        type="policy.decision",
        workflow_id="wf-1",
        trace_id=trace,
        stage_id="login",
        mode=Mode.AUTO,
        attributes={"decision": "allow", "rule": "auto edit within Intent Lock"},
    )

    events = log.read()
    assert [e.type for e in events] == ["workflow.planned", "policy.decision"]
    assert events[1].stage_id == "login"
    assert events[1].mode is Mode.AUTO
    assert events[1].attributes["decision"] == "allow"


def test_every_line_is_valid_jsonl_with_required_fields(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    log.emit(type="stage.started", workflow_id="wf-1", trace_id=new_id())

    raw_lines = events_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 1
    obj = json.loads(raw_lines[0])
    for required in ("version", "trace_id", "span_id", "timestamp", "type", "workflow_id"):
        assert required in obj


def test_appends_never_rewrite_prior_lines(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    trace = new_id()
    log.emit(type="a", workflow_id="wf-1", trace_id=trace)
    first_snapshot = events_path(tmp_path).read_text(encoding="utf-8")

    log.emit(type="b", workflow_id="wf-1", trace_id=trace)
    second_snapshot = events_path(tmp_path).read_text(encoding="utf-8")

    # The original line is still a byte-for-byte prefix of the new file.
    assert second_snapshot.startswith(first_snapshot)


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert EventLog(tmp_path).read() == []


def test_parent_span_hierarchy(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    trace = new_id()
    stage = log.emit(type="stage.span", workflow_id="wf-1", trace_id=trace)
    child = log.emit(
        type="tool.call",
        workflow_id="wf-1",
        trace_id=trace,
        parent_span_id=stage.span_id,
    )
    assert child.parent_span_id == stage.span_id
    assert isinstance(stage, Event)
