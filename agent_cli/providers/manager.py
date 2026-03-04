"""
Provider Manager — factory and cache for LLM adapters.

Reads provider configurations from ``AgentSettings`` (via ``load_providers``)
and instantiates the correct adapter (OpenAI, Anthropic, Google, etc.).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type

from agent_cli.core.config import AgentSettings, load_providers
from agent_cli.core.models.config_models import ProviderConfig
from agent_cli.data import DataRegistry
from agent_cli.memory.budget import TokenBudget, budget_for_model
from agent_cli.memory.token_counter import (
    AnthropicTokenCounter,
    BaseTokenCounter,
    GeminiTokenCounter,
    HeuristicTokenCounter,
    TiktokenCounter,
)
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.provider.anthropic_provider import AnthropicProvider
from agent_cli.providers.provider.azure_provider import AzureProvider
from agent_cli.providers.provider.google_provider import GoogleProvider
from agent_cli.providers.provider.ollama_provider import OllamaProvider
from agent_cli.providers.provider.openai_compat import OpenAICompatibleProvider
from agent_cli.providers.provider.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Registry of available adapter classes
ADAPTER_TYPES: Dict[str, Type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureProvider,
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

    def __init__(
        self,
        settings: AgentSettings,
        *,
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self._settings = settings
        self._data_registry = data_registry or DataRegistry()
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._fallback_token_counter: BaseTokenCounter = HeuristicTokenCounter(
            data_registry=self._data_registry
        )

        # load_providers() expects a dict with a "providers" key
        # We wrap settings.providers to match the signature
        self._provider_configs: Dict[str, ProviderConfig] = load_providers(
            {"providers": settings.providers},
            data_registry=self._data_registry,
        )
        self._token_counters: Dict[str, BaseTokenCounter] = self._build_token_counters()

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

    def get_runtime_identity(self, model_name: str) -> Dict[str, str]:
        """Return provider/model/deployment identity for diagnostics."""
        provider = self.get_provider(model_name)
        provider_name = str(getattr(provider, "provider_name", "") or "unknown")
        resolved_model = str(getattr(provider, "model_name", "") or model_name)
        base_url = str(getattr(provider, "base_url", "") or "").strip()
        deployment_id = self._build_deployment_id(
            provider_name=provider_name,
            model_name=resolved_model,
            base_url=base_url,
        )
        return {
            "requested_model": str(model_name),
            "provider": provider_name,
            "resolved_model": resolved_model,
            "deployment_id": deployment_id,
        }

    def get_capability_source_summary(self) -> Dict[str, str]:
        """Return a phase-0 summary of capability source ownership."""
        return {
            "declared": "model_registry",
            "observed": "capability_probe",
            "effective": "registry_snapshot_merge",
        }

    def _resolve_provider(self, model_name: str) -> BaseLLMProvider:
        """Logic for finding the correct config for a model."""
        return self._resolve_provider_v2(model_name)

    def get_token_counter(self, model_name: str) -> BaseTokenCounter:
        """Return the best token counter implementation for a model."""
        spec = self._data_registry.resolve_model_spec(model_name)
        if spec is None:
            return self._fallback_token_counter
        config = self._provider_configs.get(spec.provider)
        if config is None:
            return self._fallback_token_counter
        return self._token_counters.get(
            config.adapter_type, self._fallback_token_counter
        )

    def get_token_budget(
        self,
        model_name: str,
        *,
        response_reserve: int = 4096,
        compaction_threshold: float = 0.80,
    ) -> TokenBudget:
        """Return a TokenBudget for the target model."""
        spec = self._data_registry.resolve_model_spec(model_name)
        if spec is None:
            return budget_for_model(
                model_name,
                response_reserve=response_reserve,
                compaction_threshold=compaction_threshold,
                data_registry=self._data_registry,
            )

        config = self._provider_configs.get(spec.provider)
        max_context_override = config.max_context_tokens if config is not None else None
        return budget_for_model(
            spec.api_model,
            response_reserve=response_reserve,
            compaction_threshold=compaction_threshold,
            max_context_override=max_context_override,
            data_registry=self._data_registry,
        )

    def _resolve_provider_v2(self, model_name: str) -> BaseLLMProvider:
        """Resolve provider strictly through the v2 model registry."""
        spec = self._data_registry.resolve_model_spec(model_name)
        if spec is None:
            raise ValueError(
                "model_not_supported: "
                f"'{model_name}' is not registered in the model registry."
            )

        provider_name = str(spec.provider).strip()
        if not provider_name:
            raise ValueError(
                f"model_not_supported: model '{spec.model_id}' has no provider binding."
            )

        config = self._provider_configs.get(provider_name)
        if config is None:
            raise ValueError(
                "provider_not_configured: "
                f"provider '{provider_name}' for model '{spec.model_id}' is not configured."
            )

        resolved_model = str(spec.api_model or spec.model_id).strip()
        return self._create_provider(provider_name, config, resolved_model)

    def _create_provider(
        self, provider_name: str, config: ProviderConfig, model_name: str
    ) -> BaseLLMProvider:
        """Instantiate the adapter class defined in the config."""
        adapter_cls = ADAPTER_TYPES.get(config.adapter_type)
        if not adapter_cls:
            raise ValueError(
                f"Unknown adapter type '{config.adapter_type}' for model '{model_name}'"
            )

        api_key = self._resolve_api_key(provider_name, config)

        # OpenAICompatibleProvider accepts native_tools flag, others don't
        kwargs: Dict[str, Any] = {
            "model_name": model_name,
            "api_key": api_key,
            "base_url": config.base_url,
            "data_registry": self._data_registry,
        }
        if config.adapter_type in ["openai_compatible", "ollama"]:
            kwargs["native_tools"] = config.supports_native_tools

        provider = adapter_cls(**kwargs)
        # Inject the logical runtime name for better error reporting
        provider._runtime_provider_name = provider_name
        return provider

    def _resolve_api_key(
        self, provider_name: str, config: ProviderConfig
    ) -> Optional[str]:
        """Fetch API key considering custom env vars and AgentSettings fallbacks."""
        import os

        # If config specifies a custom env var, use it exclusively
        if config.api_key_env:
            return os.getenv(config.api_key_env)

        # Otherwise, use AgentSettings built-in resolution (which handles keyring)
        return self._settings.resolve_api_key(provider_name)

    def _build_token_counters(self) -> Dict[str, BaseTokenCounter]:
        """Build adapter-type token counters shared across providers."""
        heuristic = self._fallback_token_counter
        return {
            "openai": TiktokenCounter(
                fallback=heuristic,
                data_registry=self._data_registry,
            ),
            "azure": TiktokenCounter(
                fallback=heuristic,
                data_registry=self._data_registry,
            ),
            "anthropic": AnthropicTokenCounter(
                api_key=self._settings.resolve_api_key("anthropic"),
                fallback=heuristic,
                data_registry=self._data_registry,
            ),
            "google": GeminiTokenCounter(
                api_key=self._settings.resolve_api_key("google"),
                fallback=heuristic,
                data_registry=self._data_registry,
            ),
            "ollama": heuristic,
            "openai_compatible": heuristic,
        }

    @staticmethod
    def _build_deployment_id(
        *,
        provider_name: str,
        model_name: str,
        base_url: str = "",
    ) -> str:
        """Build a stable deployment identity key for diagnostics."""
        provider = str(provider_name).strip() or "unknown"
        model = str(model_name).strip() or "unknown"
        base = str(base_url).strip()
        if base:
            return f"{provider}:{model}@{base}"
        return f"{provider}:{model}"
