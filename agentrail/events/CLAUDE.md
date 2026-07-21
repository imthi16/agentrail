# agentrail/events/ — JSONL Timeline & Replay

Authoritative, append-only event stream at `.agentrail/events/events.jsonl`. Every
event follows OpenTelemetry GenAI-style tracing so the timeline can be replayed
locally and exported to standard observability tools.

## Event schema (per line)
Required fields on EVERY event: `trace_id`, `span_id`, `parent_span_id`,
`timestamp`, `type`, `workflow_id`, and (where applicable) `stage_id`, `mode`.
Span hierarchy:
- one **trace** per workflow,
- one **span** per stage,
- **child spans** for tool calls, shell commands, edits, and model calls.
For model calls, record `provider`, `model_id`, `tier`, and token counts / cost.

## Conventions
- Events are IMMUTABLE — corrections are new events, never edits to old lines.
- Log every policies decision (ALLOW / ASK / DENY + the rule that fired) and every
  profiles tier change.
- Make span attributes filterable: `workflow_id`, `stage_id`, `mode`, `provider`.
- Don't block the control-plane hot path on log I/O — buffer and flush async.

## Replay
The replay viewer (v0.3 React dashboard) consumes `events.jsonl` to reconstruct
the decision tree, terminal outputs, approvals, and budget burn. Keep the schema
stable and versioned so old traces stay replayable.

## Do / Don't
- DO write one JSON object per line (valid JSONL) with a schema `version` field.
- DON'T interleave partial writes — write whole lines atomically.
