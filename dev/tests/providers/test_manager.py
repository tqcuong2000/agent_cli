"""Unit tests for the ProviderManager factory."""

from types import SimpleNamespace

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.adapter_registry import AdapterRegistry
from agent_cli.core.providers.adapters.anthropic_provider import AnthropicProvider
from agent_cli.core.providers.adapters.google_provider import GoogleProvider
from agent_cli.core.providers.adapters.ollama_provider import OllamaProvider
from agent_cli.core.providers.adapters.openai_compat import OpenAICompatibleProvider
from agent_cli.core.providers.adapters.openai_provider import OpenAIProvider
from agent_cli.core.providers.cost.token_counter import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)
from agent_cli.core.providers.manager import ProviderManager


def _build_adapter_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(
        "openai",
        adapter_cls=OpenAIProvider,
        token_counter_factory=lambda _settings, data_registry, fallback: TiktokenCounter(
            fallback=fallback,
            data_registry=data_registry,
        ),
    )
    registry.register(
        "anthropic",
        adapter_cls=AnthropicProvider,
        token_counter_factory=lambda settings, data_registry, fallback: AnthropicTokenCounter(
            api_key=settings.resolve_api_key("anthropic"),
            fallback=fallback,
            data_registry=data_registry,
        ),
    )
    registry.register(
        "google",
        adapter_cls=GoogleProvider,
        token_counter_factory=lambda settings, data_registry, fallback: GeminiTokenCounter(
            api_key=settings.resolve_api_key("google"),
            fallback=fallback,
            data_registry=data_registry,
        ),
    )
    registry.register(
        "ollama",
        adapter_cls=OllamaProvider,
        token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
    )
    registry.register(
        "openai_compatible",
        adapter_cls=OpenAICompatibleProvider,
        token_counter_factory=lambda _settings, _data_registry, fallback: fallback,
    )
    return registry


def test_manager_rejects_unknown_models_without_inference() -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )

    try:
        manager.get_provider("gpt-4.5")
    except ValueError as exc:
        assert "model_not_supported" in str(exc)
    else:
        raise AssertionError("Expected model_not_supported in strict resolution mode")


def test_manager_creates_from_config():
    """Verify custom TOML provider configs are loaded."""
    settings = AgentSettings()
    settings.providers = {
        "local_vllm": {
            "adapter_type": "openai_compatible",
            "base_url": "http://localhost:8000/v1",
            "supports_native_tools": True,
        }
    }

    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )

    config = manager._provider_configs["local_vllm"]
    assert config.adapter_type == "openai_compatible"
    assert config.base_url == "http://localhost:8000/v1"


def test_manager_caching_behavior():
    """Verify that multiple requests for the same model return the same instance."""
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )

    sentinel = SimpleNamespace(provider_name="stub")
    calls: list[str] = []

    def _fake_resolve(model_name: str):
        calls.append(model_name)
        return sentinel

    manager._resolve_provider = _fake_resolve  # type: ignore[method-assign]
    p1 = manager.get_provider("arbitrary-model")
    p2 = manager.get_provider("arbitrary-model")

    # Should be the exact same object reference
    assert p1 is sentinel
    assert p1 is p2
    assert calls == ["arbitrary-model"]


def test_manager_returns_token_counters_by_provider(monkeypatch: pytest.MonkeyPatch):
    settings = AgentSettings(
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant",
        google_api_key="AIzaSy",
    )
    settings.providers = {
        "local_vllm": {
            "adapter_type": "openai_compatible",
            "base_url": "http://localhost:8000/v1",
            "supports_native_tools": True,
        }
    }
    settings.providers.update(
        {
            "openai": {"adapter_type": "openai"},
            "azure": {"adapter_type": "openai"},
            "anthropic": {"adapter_type": "anthropic"},
            "google": {"adapter_type": "google"},
        }
    )
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )

    model_specs = {
        "openai-model": SimpleNamespace(provider="openai"),
        "azure-model": SimpleNamespace(provider="azure"),
        "anthropic-model": SimpleNamespace(provider="anthropic"),
        "google-model": SimpleNamespace(provider="google"),
        "local-model": SimpleNamespace(provider="local_vllm"),
    }
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, model: model_specs.get(model),
    )

    assert isinstance(manager.get_token_counter("openai-model"), TiktokenCounter)
    assert isinstance(
        manager.get_token_counter("azure-model"),
        TiktokenCounter,
    )
    assert isinstance(
        manager.get_token_counter("anthropic-model"),
        AnthropicTokenCounter,
    )
    assert isinstance(manager.get_token_counter("google-model"), GeminiTokenCounter)
    assert isinstance(
        manager.get_token_counter("local-model"),
        HeuristicTokenCounter,
    )
    assert isinstance(
        manager.get_token_counter("unknown-model"),
        HeuristicTokenCounter,
    )


def test_manager_token_budget_uses_provider_override(monkeypatch: pytest.MonkeyPatch):
    settings = AgentSettings()
    settings.providers = {
        "openai": {
            "adapter_type": "openai",
            "max_context_tokens": 42_000,
        }
    }
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, _: SimpleNamespace(
            provider="openai",
            api_model="gpt-4o",
            model_id="openai:gpt-4o",
        ),
    )

    budget = manager.get_token_budget(
        "any-openai-model",
        response_reserve=1024,
        compaction_threshold=0.75,
    )

    assert budget.max_context == 42_000
    assert budget.response_reserve == 1024
    assert budget.compaction_threshold == 0.75


def test_manager_rejects_azure_prefixed_unknown_deployment():
    settings = AgentSettings(azure_openai_api_key="az-test-key")
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )

    try:
        manager.get_provider("azure/my-deployment")
    except ValueError as exc:
        assert "model_not_supported" in str(exc)
    else:
        raise AssertionError("Expected model_not_supported for unknown deployment")


def test_manager_token_counter_uses_model_registry_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    settings.providers.update({"openai": {"adapter_type": "openai"}})
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, _: SimpleNamespace(
            provider="openai",
            api_model="gpt-4o",
            model_id="openai:gpt-4o",
        ),
    )

    counter = manager.get_token_counter("any-openai-model")
    assert isinstance(counter, TiktokenCounter)


def test_manager_api_key_env_falls_back_to_settings_alias_values() -> None:
    settings = AgentSettings(anthropic_api_key="sk-ant-from-settings")
    settings.providers = {
        "anthropic": {
            "adapter_type": "anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
        }
    }

    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    cfg = manager._provider_configs["anthropic"]

    resolved = manager._resolve_api_key("anthropic", cfg)
    assert resolved == "sk-ant-from-settings"


def test_manager_api_key_env_normalizes_wrapped_quotes(monkeypatch) -> None:
    settings = AgentSettings()
    settings.providers = {
        "anthropic": {
            "adapter_type": "anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
        }
    }
    monkeypatch.setenv("ANTHROPIC_API_KEY", '  "sk-ant-quoted"  ')

    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    cfg = manager._provider_configs["anthropic"]

    resolved = manager._resolve_api_key("anthropic", cfg)
    assert resolved == "sk-ant-quoted"


def test_manager_unknown_adapter_type_uses_fallback_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentSettings()
    settings.providers = {"custom": {"adapter_type": "custom_adapter"}}
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, _: SimpleNamespace(
            provider="custom",
            api_model="custom-model",
            model_id="custom:model",
        ),
    )
    counter = manager.get_token_counter("custom-model")
    assert isinstance(counter, HeuristicTokenCounter)


def test_manager_uses_model_api_surface_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    settings.providers = {
        "openai": {
            "adapter_type": "openai",
        }
    }
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, _: SimpleNamespace(
            provider="openai",
            api_model="gpt-4.1-mini",
            model_id="openai:gpt-4.1-mini",
            api_surface="responses_api",
            capabilities=SimpleNamespace(
                native_tools=SimpleNamespace(supported=True),
            ),
        ),
    )

    provider = manager.get_provider("any-openai-model")
    assert isinstance(provider, OpenAIProvider)
    assert provider.api_surface == "responses_api"


def test_manager_passes_provider_api_profile_to_openai_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    settings.providers = {
        "openai": {
            "adapter_type": "openai",
            "api_profile": {
                "web_search": {
                    "chat_completions": {
                        "mutations": [
                            {"op": "append_model_suffix", "value": ":online"}
                        ]
                    }
                }
            },
        }
    }
    manager = ProviderManager(
        settings,
        adapter_registry=_build_adapter_registry(),
        data_registry=DataRegistry(),
    )
    monkeypatch.setattr(
        type(manager._data_registry),
        "resolve_model_spec",
        lambda self, _: SimpleNamespace(
            provider="openai",
            api_model="gpt-4.1-mini",
            model_id="openai:gpt-4.1-mini",
            api_surface="chat_completions",
            capabilities=SimpleNamespace(
                native_tools=SimpleNamespace(supported=True),
            ),
        ),
    )

    provider = manager.get_provider("any-openai-model")
    assert isinstance(provider, OpenAIProvider)
    assert provider._api_profile["web_search"]["chat_completions"]["mutations"][0][
        "value"
    ] == ":online"
