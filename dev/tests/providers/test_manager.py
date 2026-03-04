"""Unit tests for the ProviderManager factory."""

from agent_cli.core.config import AgentSettings
from agent_cli.memory.token_counter import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)
from agent_cli.providers.manager import ProviderManager


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
            "models": ["llama-3-8b-instruct", "mistral-large"],
            "supports_native_tools": True,
        }
    }

    manager = ProviderManager(settings)

    config = manager._provider_configs["local_vllm"]
    assert config.adapter_type == "openai_compatible"
    assert config.base_url == "http://localhost:8000/v1"
    assert "llama-3-8b-instruct" in config.models


def test_manager_caching_behavior():
    """Verify that multiple requests for the same model return the same instance."""
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(settings)

    p1 = manager.get_provider("gpt-4o")
    p2 = manager.get_provider("gpt-4o")

    # Should be the exact same object reference
    assert p1 is p2


def test_manager_returns_token_counters_by_provider():
    settings = AgentSettings(
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant",
        google_api_key="AIzaSy",
    )
    settings.providers = {
        "local_vllm": {
            "adapter_type": "openai_compatible",
            "base_url": "http://localhost:8000/v1",
            "models": ["llama-3-8b-instruct"],
            "supports_native_tools": True,
        }
    }
    manager = ProviderManager(settings)

    assert isinstance(manager.get_token_counter("gpt-4o"), TiktokenCounter)
    assert isinstance(
        manager.get_token_counter("azure/gpt-4o-deployment"), HeuristicTokenCounter
    )
    assert isinstance(
        manager.get_token_counter("claude-3-5-sonnet-20241022"),
        AnthropicTokenCounter,
    )
    assert isinstance(manager.get_token_counter("gemini-2.5-flash"), GeminiTokenCounter)
    assert isinstance(
        manager.get_token_counter("llama-3-8b-instruct"),
        HeuristicTokenCounter,
    )


def test_manager_token_budget_uses_provider_override():
    settings = AgentSettings()
    settings.providers = {
        "openai": {
            "adapter_type": "openai",
            "models": ["gpt-4o", "gpt-4o-mini", "o1", "o1-mini"],
            "max_context_tokens": 42_000,
        }
    }
    manager = ProviderManager(settings)

    budget = manager.get_token_budget(
        "gpt-4o",
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


def test_manager_token_counter_uses_model_registry_provider() -> None:
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(settings)

    counter = manager.get_token_counter("openai:gpt-4o")
    assert isinstance(counter, TiktokenCounter)
