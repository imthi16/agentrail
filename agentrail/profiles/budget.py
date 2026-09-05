"""Cost & retry budget tracking — record model-call spend and enforce ceilings.

Each model call records provider + model_id + tier + token counts + cost
estimate to the event timeline (events/CLAUDE.md), and the running total is
checked against the configured :class:`~agentrail.config.Budget`. Enforcement is
deny-closed: once a set limit is exceeded, further spend is refused and a
``budget.exceeded`` event is logged.

Costs are computed from a per-model price table (USD per 1M tokens). Prices are
volatile, so like model IDs they live in a config table, never hardcoded in hot
paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentrail.config import Budget
from agentrail.events import EventLog, new_id
from agentrail.models import Tier
from agentrail.profiles.registry import Provider

# USD per 1,000,000 tokens, as (input, output). Placeholder rates; re-verify
# against live pricing before relying on absolute figures.
PriceTable = dict[str, tuple[float, float]]

DEFAULT_PRICES: PriceTable = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "gemini-3.1-pro-preview": (2.5, 15.0),
    "gemini-3.5-flash": (0.30, 2.5),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gpt-5.6-sol": (10.0, 40.0),
    "gpt-5.6-terra": (2.0, 10.0),
    "gpt-5.6-luna": (0.50, 2.0),
    # z.ai (docs.z.ai) Sep 2026: GLM-5.3 = 1.40/4.40; Flash is radically cheap.
    "glm-5.3": (1.40, 4.40),
    "glm-5.2": (1.40, 4.40),
    "glm-5.3-flash": (0.07, 0.25),
    # OpenCode Zen/Go catalog (opencode.ai/docs/zen) Sep 2026.
    "kimi-k3": (3.0, 15.0),
    "qwen3.8-max": (2.0, 6.0),
    "qwen3.8-flash": (0.15, 0.47),
}


class BudgetExceededError(RuntimeError):
    """Raised when a model call would push spend past a configured limit."""


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    prices: PriceTable | None = None,
) -> float:
    """Estimate USD cost for a call. Unknown models cost 0 (logged upstream)."""

    table = prices or DEFAULT_PRICES
    rate = table.get(model_id)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


@dataclass
class BudgetTracker:
    """Accumulates spend and enforces the configured ceilings, deny-closed."""

    budget: Budget
    event_log: EventLog
    workflow_id: str
    prices: PriceTable = field(default_factory=lambda: DEFAULT_PRICES)
    trace_id: str | None = None
    total_usd: float = 0.0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        self._trace_id = self.trace_id or new_id()

    def remaining_usd(self) -> float | None:
        if self.budget.max_usd is None:
            return None
        return self.budget.max_usd - self.total_usd

    def remaining_tokens(self) -> int | None:
        if self.budget.max_tokens is None:
            return None
        return self.budget.max_tokens - self.total_tokens

    def would_exceed(self, cost_usd: float, tokens: int) -> bool:
        if self.budget.max_usd is not None and self.total_usd + cost_usd > self.budget.max_usd:
            return True
        if (
            self.budget.max_tokens is not None
            and self.total_tokens + tokens > self.budget.max_tokens
        ):
            return True
        return False

    def record_call(
        self,
        *,
        provider: Provider | str,
        model_id: str,
        tier: Tier,
        input_tokens: int,
        output_tokens: int,
        stage_id: str | None = None,
    ) -> float:
        """Record a model call, enforce the budget, and log the spend.

        Returns the call cost in USD. Raises :class:`BudgetExceededError`
        (after logging ``budget.exceeded``) if the call would breach a limit;
        the spend is NOT added when refused.
        """

        cost = estimate_cost(model_id, input_tokens, output_tokens, self.prices)
        tokens = input_tokens + output_tokens
        provider_name = str(provider)

        if self.would_exceed(cost, tokens):
            self.event_log.emit(
                type="budget.exceeded",
                workflow_id=self.workflow_id,
                trace_id=self._trace_id,
                stage_id=stage_id,
                attributes={
                    "provider": provider_name,
                    "model_id": model_id,
                    "tier": tier.value,
                    "attempted_usd": round(cost, 6),
                    "attempted_tokens": tokens,
                    "total_usd": round(self.total_usd, 6),
                    "total_tokens": self.total_tokens,
                    "max_usd": self.budget.max_usd,
                    "max_tokens": self.budget.max_tokens,
                },
            )
            raise BudgetExceededError(
                f"model call refused: would exceed budget "
                f"(usd={self.budget.max_usd}, tokens={self.budget.max_tokens})"
            )

        self.total_usd += cost
        self.total_tokens += tokens
        self.event_log.emit(
            type="model.call",
            workflow_id=self.workflow_id,
            trace_id=self._trace_id,
            stage_id=stage_id,
            attributes={
                "provider": provider_name,
                "model_id": model_id,
                "tier": tier.value,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
                "total_usd": round(self.total_usd, 6),
                "total_tokens": self.total_tokens,
            },
        )
        return cost
