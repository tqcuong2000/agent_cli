"""
Configuration models used across the system.

Kept separate from the main settings class so that other modules
can import lightweight types without pulling in the entire Pydantic
Settings machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ProtocolMode(str, Enum):
    """Protocol mode for agent/system communication."""

    JSON_ONLY = "json_only"


class EffortLevel(str, Enum):
    """Canonical reasoning-effort levels across providers."""

    AUTO = "auto"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"

    @classmethod
    def _missing_(cls, value: object) -> "EffortLevel | None":
        """Allow case-insensitive parsing from user/config input."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            for level in cls:
                if level.value == normalized:
                    return level
        return None


def effort_values() -> tuple[str, ...]:
    """Return allowed effort string values."""
    return tuple(level.value for level in EffortLevel)


def normalize_effort(value: str | EffortLevel | None) -> EffortLevel:
    """Normalize user/config effort input to the canonical enum."""
    if isinstance(value, EffortLevel):
        return value
    if value is None:
        return EffortLevel.AUTO
    return EffortLevel(str(value))


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider.

    Built-in providers (openai, anthropic, google) are auto-registered.
    Users add custom providers via TOML ``[providers.<name>]`` sections.
    """

    adapter_type: (
        str  # "openai" | "anthropic" | "google" | "openai_compatible" | "ollama"
    )
    base_url: Optional[str] = None  # Required for openai_compatible
    api_key_env: Optional[str] = None  # Custom env var name for API key
    supports_native_tools: bool = True
    max_context_tokens: Optional[int] = None  # Override default context window
    api_profile: Dict[str, Any] = field(default_factory=dict)
    require_verification: bool = True


@dataclass
class NativeToolsCapabilitySpec:
    """Typed capability payload for native local tool-calling support."""

    supported: bool


@dataclass
class EffortCapabilitySpec:
    """Typed capability payload for reasoning-effort controls."""

    supported: bool
    levels: List[str] = field(default_factory=lambda: [EffortLevel.AUTO.value])


@dataclass
class WebSearchCapabilitySpec:
    """Typed capability payload for provider-managed web search."""

    supported: bool
    tool_type: str = ""


@dataclass
class CapabilitySpec:
    """Typed capabilities for a model preset entry."""

    native_tools: NativeToolsCapabilitySpec
    effort: EffortCapabilitySpec
    web_search: WebSearchCapabilitySpec


@dataclass
class ProviderSpec:
    """Built-in provider specification loaded from data files."""

    name: str
    adapter_type: str
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    max_context_tokens: Optional[int] = None
    api_profile: Dict[str, Any] = field(default_factory=dict)
    require_verification: bool = True


@dataclass
class ModelSpec:
    """Model preset entry loaded from data files."""

    model_id: str
    provider: str
    model_ref: str
    api_model: str
    api_surface: str = ""
    plain_text: bool = False
    context_window: int = 128_000
    tokenizer: str = "cl100k_base"
    pricing_input: float = 0.0
    pricing_output: float = 0.0
    capabilities: CapabilitySpec = field(
        default_factory=lambda: CapabilitySpec(
            native_tools=NativeToolsCapabilitySpec(supported=False),
            effort=EffortCapabilitySpec(supported=False),
            web_search=WebSearchCapabilitySpec(supported=False),
        )
    )


@dataclass
class ModelResolution:
    """Resolved model identity used by provider routing and diagnostics."""

    requested_model: str
    model_id: str
    provider: str
    api_model: str
    deployment_id: str = ""


@dataclass
class CapabilityObservation:
    """Observed runtime capability state for provider/model/deployment key."""

    status: str  # supported | unsupported | unknown
    reason: str = ""
    checked_at: datetime | None = None
    source: str = "declared"


@dataclass
class CapabilitySnapshot:
    """Combined declared + observed + effective capability state."""

    provider: str
    model: str
    deployment_id: str
    declared: CapabilitySpec
    observed: Dict[str, CapabilityObservation] = field(default_factory=dict)
    effective: Dict[str, CapabilityObservation] = field(default_factory=dict)
