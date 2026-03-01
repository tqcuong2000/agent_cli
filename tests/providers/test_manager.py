"""
Unit tests for the ProviderManager factory.
"""

from typing import Any, Dict

from agent_cli.core.config import AgentSettings
from agent_cli.memory.token_counter import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)
from agent_cli.providers.manager import ProviderManager
from agent_cli.providers.provider.anthropic_provider import AnthropicProvider
from agent_cli.providers.provider.google_provider import GoogleProvider
from agent_cli.providers.provider.openai_compat import OpenAICompatibleProvider
from agent_cli.providers.provider.openai_provider import OpenAIProvider


def test_manager_infers_known_model_prefixes():
    """Verify inference for standard models (gpt, claude, gemini)."""
    settings = AgentSettings(
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant",
        google_api_key="AIzaSy",
    )
    manager = ProviderManager(settings)

    p1 = manager._infer_provider("gpt-4.5")
    assert isinstance(p1, OpenAIProvider)
    assert p1.provider_name == "openai"
    assert p1.model_name == "gpt-4.5"

    p2 = manager._infer_provider("claude-sonnet-4.6")
    assert isinstance(p2, AnthropicProvider)
    assert p2.provider_name == "anthropic"

    p3 = manager._infer_provider("gemini-2.5-pro")
    assert isinstance(p3, GoogleProvider)
    assert p3.provider_name == "google"


def test_manager_creates_from_config():
    """Verify custom TOML configurations are respected."""
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

    # "llama-3-8b-instruct" mapped to the custom local_vllm provider
    provider = manager.get_provider("llama-3-8b-instruct")

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "local_vllm"
    assert provider.model_name == "llama-3-8b-instruct"
    assert provider.supports_native_tools is True
    assert provider.client.base_url == "http://localhost:8000/v1/"


def test_manager_caching_behavior():
    """Verify that multiple requests for the same model return the same instance."""
    settings = AgentSettings(openai_api_key="sk-test")
    manager = ProviderManager(settings)

    p1 = manager.get_provider("gpt-5")
    p2 = manager.get_provider("gpt-5")

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
        "custom_provider": {
            "adapter_type": "openai_compatible",
            "base_url": "http://localhost:8000/v1",
            "models": ["custom-model-a"],
            "max_context_tokens": 42_000,
        }
    }
    manager = ProviderManager(settings)

    budget = manager.get_token_budget(
        "custom-model-a",
        response_reserve=1024,
        compaction_threshold=0.75,
    )

    assert budget.max_context == 42_000
    assert budget.response_reserve == 1024
    assert budget.compaction_threshold == 0.75
