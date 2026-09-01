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
- Gotcha (measured): Sol's tool-call success is ~91% vs Terra's ~97% — harness
  loops favor Terra; Luna's long-context recall falls off a cliff (~41% MRCR),
  never bind review to Luna.

z.ai (verified docs.z.ai + z.ai/blog, Aug 2026):
- Deep → `glm-5.3` (open-weights #1 coding: TerminalBench 88.2, CyberGym #1;
  ~30–50% fewer output tokens than 5.2). Thinking mandatory; Low/High/Max.
- Balanced → `glm-5.2` (MIT open weights, 1M ctx).
- Fast → `glm-5.3-flash` ($0.07/$0.25 per 1M — the cheapest frontier-grade fast).

OpenCode Go/Zen (verified opencode.ai/docs/zen catalog, Aug–Sep 2026):
- Deep → `kimi-k3` (repo-scale coding, 1M ctx); Balanced → `qwen3.8-max`;
  Fast → `qwen3.8-flash`. NEVER pin deprecated IDs (`kimi-k2.x`, `glm-5.1`).
- Endpoint is OpenAI-compatible `/chat/completions` over
  `https://opencode.ai/go/v1` (default) or `https://opencode.ai/zen/v1`; key
  arrives via the env var named in `config.opencode.api_key_env`, never in YAML.

## Role layer (on top of provider+tier)
The pipeline is role-driven for the solo developer: `plan`, `research`, `code`,
`review` — each binds to `(provider, tier)` via `config.roles` (`RoleBind`).
`ModelRouter.route_role` resolves and logs a `profile.tier_changed` event with a
`role` attribute; unbound roles fall back to work-kind routing. VERIFY and
MARKET slot into the same table later with no schema change.

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
