"""Budget tracker: cost estimate, spend accounting, deny-closed enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrail.config import Budget
from agentrail.events import EventLog
from agentrail.models import Tier
from agentrail.profiles import (
    BudgetExceededError,
    BudgetTracker,
    Provider,
    estimate_cost,
)


def test_estimate_cost_uses_price_table() -> None:
    # haiku: (0.80 in, 4.0 out) per 1M tokens.
    cost = estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert cost == pytest.approx(4.80)


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert estimate_cost("mystery-model", 1000, 1000) == 0.0


def _tracker(tmp_path: Path, **budget: object) -> BudgetTracker:
    return BudgetTracker(
        budget=Budget(**budget),  # type: ignore[arg-type]
        event_log=EventLog(tmp_path),
        workflow_id="wf-1",
    )


def test_record_call_accumulates_and_logs(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    cost = tracker.record_call(
        provider=Provider.ANTHROPIC,
        model_id="claude-sonnet-4-6",
        tier=Tier.BALANCED,
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert cost == pytest.approx(3.0)
    assert tracker.total_usd == pytest.approx(3.0)
    assert tracker.total_tokens == 1_000_000

    events = [e for e in EventLog(tmp_path).read() if e.type == "model.call"]
    assert len(events) == 1
    assert events[0].attributes["cost_usd"] == pytest.approx(3.0)


def test_usd_limit_is_deny_closed(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path, max_usd=2.0)
    with pytest.raises(BudgetExceededError):
        tracker.record_call(
            provider=Provider.ANTHROPIC,
            model_id="claude-sonnet-4-6",  # 3 USD for 1M input
            tier=Tier.BALANCED,
            input_tokens=1_000_000,
            output_tokens=0,
        )
    # Refused spend is NOT accumulated.
    assert tracker.total_usd == 0.0
    types = [e.type for e in EventLog(tmp_path).read()]
    assert "budget.exceeded" in types
    assert "model.call" not in types


def test_token_limit_is_deny_closed(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path, max_tokens=500)
    with pytest.raises(BudgetExceededError):
        tracker.record_call(
            provider=Provider.OPENAI,
            model_id="gpt-5.6-luna",
            tier=Tier.FAST,
            input_tokens=400,
            output_tokens=200,  # 600 > 500
        )
    assert tracker.total_tokens == 0


def test_calls_within_budget_are_allowed(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path, max_usd=10.0, max_tokens=5_000_000)
    for _ in range(3):
        tracker.record_call(
            provider=Provider.OPENAI,
            model_id="gpt-5.6-terra",  # 2 USD per 1M input
            tier=Tier.BALANCED,
            input_tokens=1_000_000,
            output_tokens=0,
        )
    assert tracker.total_usd == pytest.approx(6.0)
    assert tracker.remaining_usd() == pytest.approx(4.0)
    assert tracker.remaining_tokens() == 2_000_000


def test_unbounded_budget_returns_none_remaining(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    assert tracker.remaining_usd() is None
    assert tracker.remaining_tokens() is None
