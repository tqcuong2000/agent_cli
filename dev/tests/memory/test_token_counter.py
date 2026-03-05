"""Unit tests for token counter implementations."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.cost.token_counter import (
    AnthropicTokenCounter,
    BaseTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)


class StubCounter(BaseTokenCounter):
    def __init__(self, value: int) -> None:
        self.value = value
        self.calls = 0

    def count(self, messages, model_name: str) -> int:  # type: ignore[override]
        self.calls += 1
        return self.value


def test_heuristic_counter_counts_nonzero():
    counter = HeuristicTokenCounter(data_registry=DataRegistry())
    messages = [{"role": "user", "content": "hello world"}]
    assert counter.count(messages, "any-model") > 0


def test_tiktoken_counter_falls_back_when_module_unavailable(monkeypatch):
    fallback = StubCounter(77)
    counter = TiktokenCounter(fallback=fallback, data_registry=DataRegistry())

    monkeypatch.setitem(sys.modules, "tiktoken", None)
    result = counter.count([{"role": "user", "content": "hi"}], "gpt-4o")

    assert result == 77
    assert fallback.calls == 1


def test_tiktoken_counter_picks_o200k_for_new_openai_models(monkeypatch):
    captured: dict[str, str] = {}

    class FakeEncoding:
        def encode(self, text: str):
            return [text]

    def fake_get_encoding(name: str):
        captured["encoding"] = name
        return FakeEncoding()

    monkeypatch.setitem(
        sys.modules, "tiktoken", SimpleNamespace(get_encoding=fake_get_encoding)
    )

    registry = DataRegistry()
    counter = TiktokenCounter(data_registry=registry)
    _ = counter.count([{"role": "user", "content": "hi"}], "gpt-4o-mini")

    assert captured["encoding"] == registry.get_tokenizer_encoding("gpt-4o-mini")


def test_anthropic_counter_uses_count_tokens_api(monkeypatch):
    fallback = StubCounter(12)
    counter = AnthropicTokenCounter(
        api_key="test",
        fallback=fallback,
        data_registry=DataRegistry(),
    )

    call_args: dict[str, object] = {}

    def fake_count_tokens(**kwargs):
        call_args.update(kwargs)
        return SimpleNamespace(input_tokens=321)

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(count_tokens=fake_count_tokens)
    )
    monkeypatch.setattr(counter, "_get_client", lambda: fake_client)

    result = counter.count(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        "claude-3-5-sonnet-20241022",
    )

    assert result == 321
    assert call_args["model"] == "claude-3-5-sonnet-20241022"
    assert call_args["system"] == "system"
    assert fallback.calls == 0


def test_gemini_counter_falls_back_when_api_errors(monkeypatch):
    fallback = StubCounter(55)
    counter = GeminiTokenCounter(
        api_key="test",
        fallback=fallback,
        data_registry=DataRegistry(),
    )

    class FakeModels:
        @staticmethod
        def count_tokens(**kwargs):
            raise RuntimeError("boom")

    fake_client = SimpleNamespace(models=FakeModels())
    monkeypatch.setattr(counter, "_get_client", lambda: fake_client)

    result = counter.count([{"role": "user", "content": "hi"}], "gemini-2.5-flash")

    assert result == 55
    assert fallback.calls == 1
