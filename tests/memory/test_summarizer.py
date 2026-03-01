"""Tests for adaptive summarization memory compaction."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pytest

from agent_cli.memory.budget import TokenBudget
from agent_cli.memory.summarizer import SummarizingMemoryManager
from agent_cli.memory.token_counter import BaseTokenCounter


class SimpleCounter(BaseTokenCounter):
    """Deterministic token counter based on content length."""

    def count(self, messages: Sequence[Dict[str, Any]], model_name: str) -> int:
        total = 0
        for message in messages:
            total += len(str(message.get("content", "")))
        return total


class SummaryProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: List[Dict[str, Any]] = []

    async def safe_generate(self, **kwargs: Any):
        self.calls.append(kwargs)
        return type("Resp", (), {"text_content": self.text})()


def _append_turn(
    manager: SummarizingMemoryManager, idx: int, *, include_tool: bool = False
):
    manager.add_working_event({"role": "user", "content": f"user-{idx} " + ("x" * 60)})
    manager.add_working_event(
        {"role": "assistant", "content": f"assistant-{idx} " + ("y" * 60)}
    )
    if include_tool:
        manager.add_working_event(
            {
                "role": "tool",
                "content": (
                    "[Tool: read_file] Result:\n"
                    f"opened src/module_{idx}.py and docs/notes_{idx}.md"
                ),
            }
        )


@pytest.mark.asyncio
async def test_summarizer_preserves_system_and_recent_turns():
    provider = SummaryProvider("Goals:\n- keep context\nDecisions:\n- proceed")

    manager = SummarizingMemoryManager(
        token_counter=SimpleCounter(),
        token_budget=TokenBudget(
            max_context=500, response_reserve=0, compaction_threshold=0.7
        ),
        model_name="gpt-4o",
        keep_recent_turns=2,
        summarization_model="gpt-4o-mini",
        summarizer_provider_factory=lambda model: provider,
        summary_response_tokens=300,
    )

    manager.add_working_event({"role": "system", "content": "system prompt"})
    for i in range(8):
        _append_turn(manager, i)

    assert manager.should_compact() is True
    await manager.summarize_and_compact()

    context = manager.get_working_context()
    assert context[0]["role"] == "system"
    assert context[0]["content"] == "system prompt"
    assert any("[Context Summary]" in str(msg.get("content", "")) for msg in context)
    assert any(msg.get("content", "").startswith("user-7") for msg in context)
    assert any(msg.get("content", "").startswith("assistant-7") for msg in context)

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["max_tokens"] == 300
    assert call["tools"] is None


@pytest.mark.asyncio
async def test_summarizer_uses_heuristic_fallback_when_provider_unavailable():
    manager = SummarizingMemoryManager(
        token_counter=SimpleCounter(),
        token_budget=TokenBudget(
            max_context=1600, response_reserve=0, compaction_threshold=0.7
        ),
        model_name="gpt-4o",
        keep_recent_turns=1,
        summarization_model="missing-provider-model",
        summarizer_provider_factory=lambda model: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
    )

    manager.add_working_event({"role": "system", "content": "system prompt"})
    for i in range(6):
        _append_turn(manager, i, include_tool=True)

    await manager.summarize_and_compact()
    context = manager.get_working_context()

    summary_messages = [
        msg for msg in context if "[Context Summary]" in str(msg.get("content", ""))
    ]
    assert len(summary_messages) == 1
    summary_text = summary_messages[0]["content"]
    assert "Tool Usage:" in summary_text
    assert "Files Mentioned:" in summary_text
    assert "read_file" in summary_text


@pytest.mark.asyncio
async def test_summarizer_uses_configured_cheap_model_name():
    requested_models: List[str] = []
    provider = SummaryProvider("Short summary")

    def provider_factory(model_name: str):
        requested_models.append(model_name)
        return provider

    manager = SummarizingMemoryManager(
        token_counter=SimpleCounter(),
        token_budget=TokenBudget(
            max_context=400, response_reserve=0, compaction_threshold=0.7
        ),
        model_name="claude-3-5-sonnet",
        keep_recent_turns=1,
        summarization_model="gpt-4o-mini",
        summarizer_provider_factory=provider_factory,
    )

    manager.add_working_event({"role": "system", "content": "system prompt"})
    for i in range(4):
        _append_turn(manager, i)

    await manager.summarize_and_compact()
    assert requested_models == ["gpt-4o-mini"]
