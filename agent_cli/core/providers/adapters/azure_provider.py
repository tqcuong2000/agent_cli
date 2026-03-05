"""
Azure OpenAI Provider.

This adapter reuses the OpenAI chat-completions implementation while exposing
Azure as a first-class provider key in config and telemetry.
"""

from __future__ import annotations

from agent_cli.core.providers.adapters.openai_provider import OpenAIProvider


class AzureProvider(OpenAIProvider):
    """Adapter for Azure OpenAI endpoints."""

    @property
    def provider_name(self) -> str:
        return self._runtime_provider_name or "azure"
