"""Unit tests for token budgeting and reactive model switching."""

from __future__ import annotations

from typing import Any, Dict, Sequence

import pytest

from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.memory.budget import (
    TokenBudget,
    budget_for_model,
    infer_model_max_context,
)
from agent_cli.memory.token_counter import BaseTokenCounter


class SimpleCounter(BaseTokenCounter):
    """Deterministic token counter: count content string lengths."""

    def count(self, messages: Sequence[Dict[str, Any]], model_name: str) -> int:
        total = 0
        for msg in messages:
            total += len(str(msg.get("content", "")))
        return total


def test_token_budget_available_for_context():
    budget = TokenBudget(max_context=128_000, response_reserve=4096)
    assert budget.available_for_context() == 123_904


def test_token_budget_should_compact_threshold():
    budget = TokenBudget(
        max_context=10_000,
        response_reserve=1000,
        compaction_threshold=0.80,
    )
    # available = 9000, threshold = 7200
    assert budget.should_compact(7199) is False
    assert budget.should_compact(7200) is True


def test_budget_lookup_examples():
    assert infer_model_max_context("gpt-4o") == 128_000
    assert infer_model_max_context("claude-3-5-sonnet-20241022") == 200_000
    assert infer_model_max_context("gemini-1.5-pro") == 2_000_000


def test_budget_for_model_override_wins():
    budget = budget_for_model(
        "my-model",
        response_reserve=2048,
        compaction_threshold=0.75,
        max_context_override=50_000,
    )
    assert budget.max_context == 50_000
    assert budget.response_reserve == 2048
    assert budget.compaction_threshold == 0.75


@pytest.mark.asyncio
async def test_working_memory_reacts_to_smaller_model_budget():
    counter = SimpleCounter()
    memory = WorkingMemoryManager(
        keep_recent=3,
        token_counter=counter,
        token_budget=TokenBudget(
            max_context=5000, response_reserve=0, compaction_threshold=0.80
        ),
        model_name="gpt-4o",
    )

    memory.add_working_event({"role": "system", "content": "sys"})
    for i in range(8):
        memory.add_working_event({"role": "user", "content": f"msg{i}-" + ("x" * 50)})

    before_count = memory.message_count
    assert before_count == 9

    compacted = await memory.on_model_changed(
        "tiny-model",
        token_counter=counter,
        token_budget=TokenBudget(
            max_context=300, response_reserve=0, compaction_threshold=0.80
        ),
    )

    assert compacted is True
    assert memory.message_count < before_count
    assert any(
        "Context compacted" in str(m.get("content", ""))
        for m in memory.get_working_context()
    )
