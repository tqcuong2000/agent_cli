"""
Configuration models used across the system.

Kept separate from the main settings class so that other modules
can import lightweight types without pulling in the entire Pydantic
Settings machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class EffortLevel(str, Enum):
    """Agent reasoning effort — controls iteration limits and tool budgets."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    XHIGH = "XHIGH"


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider.

    Built-in providers (openai, anthropic, google) are auto-registered.
    Users add custom providers via TOML ``[providers.<name>]`` sections.
    """

    adapter_type: str  # "openai" | "anthropic" | "google" | "openai_compatible"
    base_url: Optional[str] = None  # Required for openai_compatible
    models: List[str] = field(default_factory=list)
    api_key_env: Optional[str] = None  # Custom env var name for API key
    default_model: Optional[str] = None
    supports_native_tools: bool = True
    max_context_tokens: Optional[int] = None  # Override default context window
