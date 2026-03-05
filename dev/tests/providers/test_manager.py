"""Unit tests for the ProviderManager factory."""

from types import MappingProxyType, SimpleNamespace

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.providers.cost.token_counter import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)
import agent_cli.core.providers.manager as manager_module
from agent_cli.core.providers.manager import ADAPTER_TYPES, ProviderManager


def test_manager_rejects_unknown_models_without_inference() -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(settings)

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

    manager = ProviderManager(settings)

    config = manager._provider_configs["local_vllm"]
    assert config.adapter_type == "openai_compatible"
    assert config.base_url == "http://localhost:8000/v1"


def test_manager_caching_behavior():
    """Verify that multiple requests for the same model return the same instance."""
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(settings)

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
            "azure": {"adapter_type": "azure"},
            "anthropic": {"adapter_type": "anthropic"},
            "google": {"adapter_type": "google"},
        }
    )
    manager = ProviderManager(settings)

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
    manager = ProviderManager(settings)
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
    manager = ProviderManager(settings)

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
    manager = ProviderManager(settings)
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

    manager = ProviderManager(settings)
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

    manager = ProviderManager(settings)
    cfg = manager._provider_configs["anthropic"]

    resolved = manager._resolve_api_key("anthropic", cfg)
    assert resolved == "sk-ant-quoted"


def test_adapter_types_is_immutable() -> None:
    with pytest.raises(TypeError):
        ADAPTER_TYPES["custom"] = object  # type: ignore[index]


def test_manager_validates_adapter_types_empty_key(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = MappingProxyType({"": next(iter(ADAPTER_TYPES.values()))})
    monkeypatch.setattr(manager_module, "ADAPTER_TYPES", invalid)

    with pytest.raises(RuntimeError, match="Empty adapter type key"):
        ProviderManager(AgentSettings())


def test_manager_validates_adapter_types_invalid_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = MappingProxyType({"openai": object})  # type: ignore[dict-item]
    monkeypatch.setattr(manager_module, "ADAPTER_TYPES", invalid)

    with pytest.raises(RuntimeError, match="must inherit BaseLLMProvider"):
        ProviderManager(AgentSettings())
