"""Unit tests for AdapterRegistry."""

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.adapter_registry import AdapterRegistry
from agent_cli.core.providers.adapters.openai_provider import OpenAIProvider
from agent_cli.core.providers.cost.token_counter import HeuristicTokenCounter


def test_adapter_registry_register_and_lookup() -> None:
    registry = AdapterRegistry()
    registry.register(
        "openai",
        adapter_cls=OpenAIProvider,
        token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
    )

    assert registry.get_adapter_class("openai") is OpenAIProvider

    fallback = HeuristicTokenCounter(data_registry=DataRegistry())
    built = registry.build_token_counter(
        "openai",
        settings=AgentSettings(),
        data_registry=DataRegistry(),
        fallback=fallback,
    )
    assert built is fallback


def test_adapter_registry_rejects_empty_key() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        registry.register(
            "",
            adapter_cls=OpenAIProvider,
            token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
        )


def test_adapter_registry_rejects_invalid_base_class() -> None:
    registry = AdapterRegistry()
    with pytest.raises(ValueError, match="must inherit BaseLLMProvider"):
        registry.register(
            "invalid",
            adapter_cls=object,  # type: ignore[arg-type]
            token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
        )


def test_adapter_registry_rejects_duplicates() -> None:
    registry = AdapterRegistry()
    registry.register(
        "openai",
        adapter_cls=OpenAIProvider,
        token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
    )
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            "openai",
            adapter_cls=OpenAIProvider,
            token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
        )


def test_adapter_registry_freeze_lifecycle() -> None:
    registry = AdapterRegistry()
    registry.register(
        "openai",
        adapter_cls=OpenAIProvider,
        token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
    )
    registry.freeze()
    registry.freeze()

    assert registry.is_frozen is True
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(
            "anthropic",
            adapter_cls=OpenAIProvider,
            token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
        )


def test_adapter_registry_freeze_rejects_empty_registry() -> None:
    registry = AdapterRegistry()
    with pytest.raises(RuntimeError, match="at least one adapter"):
        registry.freeze()
