"""
Provider Manager — factory and cache for LLM adapters.

Reads provider configurations from ``AgentSettings`` (via ``load_providers``)
and instantiates the correct adapter (OpenAI, Anthropic, Google, etc.).
Includes fallback inference if a model isn't explicitly configured.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Type

from agent_cli.core.config import AgentSettings, load_providers
from agent_cli.core.models.config_models import ProviderConfig
from agent_cli.providers.provider.anthropic_provider import AnthropicProvider
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.provider.google_provider import GoogleProvider
from agent_cli.providers.provider.ollama_provider import OllamaProvider
from agent_cli.providers.provider.openai_compat import OpenAICompatibleProvider
from agent_cli.providers.provider.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Registry of available adapter classes
ADAPTER_TYPES: Dict[str, Type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


class ProviderManager:
    """Factory and cache for LLM provider adapters.

    Reads configuration from the central ``AgentSettings`` object to
    instantiate adapters with the correct API keys, URLs, and tool modes.
    Caches provider instances so successive requests to the same model
    don't rebuild the adapter.
    """

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._providers: Dict[str, BaseLLMProvider] = {}

        # load_providers() expects a dict with a "providers" key
        # We wrap settings.providers to match the signature
        self._provider_configs: Dict[str, ProviderConfig] = load_providers(
            {"providers": settings.providers}
        )

    def get_provider(self, model_name: str) -> BaseLLMProvider:
        """Get or create a provider instance for the given model_name."""
        if model_name in self._providers:
            return self._providers[model_name]

        provider = self._resolve_provider(model_name)
        self._providers[model_name] = provider
        logger.debug(
            f"Instantiated {provider.provider_name} adapter for '{model_name}'"
        )
        return provider

    def _resolve_provider(self, model_name: str) -> BaseLLMProvider:
        """Logic for finding the correct config for a model."""
        # 1. Exact match in configured models list or default_model
        for name, config in self._provider_configs.items():
            if model_name in config.models or model_name == config.default_model:
                return self._create_provider(name, config, model_name)

        # 2. Match by provider name prefix (e.g. "openai" → OpenAIProvider)
        for name, config in self._provider_configs.items():
            for sep in ["/", ":"]:
                prefix = f"{name}{sep}"
                if model_name.startswith(prefix):
                    # Strip only the matched prefix
                    actual_model = model_name[len(prefix) :]
                    return self._create_provider(name, config, actual_model)

        # 3. Fallback inference based on common model roots
        return self._infer_provider(model_name)

    def _create_provider(
        self, provider_name: str, config: ProviderConfig, model_name: str
    ) -> BaseLLMProvider:
        """Instantiate the adapter class defined in the config."""
        adapter_cls = ADAPTER_TYPES.get(config.adapter_type)
        if not adapter_cls:
            raise ValueError(
                f"Unknown adapter type '{config.adapter_type}' "
                f"for model '{model_name}'"
            )

        api_key = self._resolve_api_key(provider_name, config)

        # OpenAICompatibleProvider accepts native_tools flag, others don't
        kwargs: Dict[str, Any] = {
            "model_name": model_name,
            "api_key": api_key,
            "base_url": config.base_url,
        }
        if config.adapter_type in ["openai_compatible", "ollama"]:
            kwargs["native_tools"] = config.supports_native_tools

        provider = adapter_cls(**kwargs)
        # Inject the logical runtime name for better error reporting
        provider._runtime_provider_name = provider_name
        return provider

    def _resolve_api_key(self, provider_name: str, config: ProviderConfig) -> Optional[str]:
        """Fetch API key considering custom env vars and AgentSettings fallbacks."""
        import os

        # If config specifies a custom env var, use it exclusively
        if config.api_key_env:
            return os.getenv(config.api_key_env)

        # Otherwise, use AgentSettings built-in resolution (which handles keyring)
        return self._settings.resolve_api_key(provider_name)

    def _infer_provider(self, model_name: str) -> BaseLLMProvider:
        """Fallback heuristics for unknown models not in config."""
        lower_model = model_name.lower()

        if "gpt-" in lower_model or "o1" in lower_model or "o3" in lower_model:
            config = self._provider_configs.get("openai")
            if config:
                return self._create_provider("openai", config, model_name)
            return OpenAIProvider(model_name, self._settings.openai_api_key)

        elif "claude" in lower_model:
            config = self._provider_configs.get("anthropic")
            if config:
                return self._create_provider("anthropic", config, model_name)
            return AnthropicProvider(model_name, self._settings.anthropic_api_key)

        elif "gemini" in lower_model:
            config = self._provider_configs.get("google")
            if config:
                return self._create_provider("google", config, model_name)
            return GoogleProvider(model_name, self._settings.google_api_key)

        raise ValueError(
            f"Cannot strictly infer provider for model '{model_name}'. "
            f"Please register it in config.toml under [providers.<name>]."
        )
