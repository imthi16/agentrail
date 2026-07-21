# agentrail/profiles/ — Model / Provider / Reasoning Picker

The picker is TWO steps: (1) choose family/provider, (2) choose effort tier
**Deep / Balanced / Fast**. Never expose raw model strings directly in the UI.
Each tier maps to a real model + reasoning budget via a config table so volatile
model IDs live in ONE place.

## Verified current model IDs (as of July 2026) — DO NOT use the ChatGPT spec's names
Anthropic — NOTE: **"Claude 3.5 Opus" does NOT exist.**
- Deep     → `claude-opus-4-8` (hardest work; `claude-fable-5` for max quality)
- Balanced → `claude-sonnet-4-6` (or the newer Sonnet 5)
- Fast     → `claude-haiku-4-5-20251001` (the date suffix is PART of the ID)
- Claude reasoning depth is set via extended-thinking budget / the Claude Code
  "Effort" selector — there is no `reasoning_effort` field.

Google Gemini — these names ARE real (spec was wrong to flag them as fake):
- Deep     → `gemini-3.1-pro-preview`, `thinking_level="HIGH"` (Deep Think Mini)
- Balanced → `gemini-3.5-flash`, `thinking_level="MEDIUM"` (newest; strong on code)
- Fast     → `gemini-3.1-flash-lite`, `thinking_level="MINIMAL"` or `"LOW"`
- `thinking_level` (MINIMAL/LOW/MEDIUM/HIGH) REPLACES the old numeric
  `thinking_budget` (0–24576); sending BOTH → HTTP 400. MINIMAL is Flash/
  Flash-Lite only.

OpenAI / Codex:
- `reasoning_effort`: GPT-5 = minimal/low/medium/high; GPT-5.6 = none/low/medium/
  high/xhigh/max (default medium). Models: `gpt-5.6-sol` (Deep),
  `gpt-5.6-terra` (Balanced), `gpt-5.6-luna` (Fast).

## Config-driven mapping (single source of truth)
Store tiers in config (e.g. `.agentrail/config.yaml` or a bundled default) as
`{provider: {tier: {model_id, thinking_param}}}`. Code paths reference
`(provider, tier)` — never a hardcoded model string.

## Auto-downgrade policy
- Planning / architecture → Deep. Implementation → Balanced.
  Lint/format/trivial fixes → Fast.
- The router may downgrade mid-workflow; log every tier change to agentrail/events
  (record provider + model_id + tier + token/cost estimate).

## Do / Don't
- DO re-verify model IDs against each provider's live model list before pinning in
  production — H1 2026 rotated these repeatedly.
- DON'T hardcode model aliases in reproducible code paths; go through the config
  table.
