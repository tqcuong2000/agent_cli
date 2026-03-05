"""
Configuration System — TOML-based hierarchical config with Pydantic validation.

Loading order (lowest → highest precedence):

1. Factory Defaults    Field defaults in ``AgentSettings``
2. Global TOML         ``~/.agent_cli/config.toml``
3. Local Workspace     ``<project>/.agent_cli/settings.toml``
4. ``.env`` file       Loaded by Pydantic-Settings
5. Environment vars    ``AGENT_*`` prefix
6. CLI flags           Passed at instantiation via ``__init__``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

import tomllib
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from agent_cli.core.infra.config.config_models import (
    EffortLevel,
    ProtocolMode,
    ProviderConfig,
    effort_values,
)
from agent_cli.core.infra.registry.registry import DataRegistry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Custom TOML Settings Source (1.3.2 — Tri-Layer Merge)
# ══════════════════════════════════════════════════════════════════════


class TomlConfigSettingsSource(PydanticBaseSettingsSource):
    """Loads and deep-merges Global TOML + Local Workspace TOML.

    Merge order: global values are loaded first, then local values
    overwrite any duplicates (including nested dicts like ``[providers.*]``).
    """

    def __init__(
        self,
        settings_cls: Type[BaseSettings],
        global_path: Path,
        local_path: Path | None = None,
    ) -> None:
        super().__init__(settings_cls)
        self._global_path = global_path
        self._local_path = local_path
        self._merged: Dict[str, Any] | None = None

    # ── PydanticBaseSettingsSource interface ──────────────────────

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        merged = self._get_merged()
        val = merged.get(field_name)
        return val, field_name, val is not None

    def __call__(self) -> Dict[str, Any]:
        return self._get_merged()

    # ── Internal ─────────────────────────────────────────────────

    def _get_merged(self) -> Dict[str, Any]:
        """Lazy-load and cache the merged TOML data."""
        if self._merged is None:
            global_data = self._load_toml(self._global_path)
            local_data = self._load_toml(self._local_path) if self._local_path else {}
            self._merged = _deep_merge(global_data, local_data)
        return self._merged

    @staticmethod
    def _load_toml(path: Path) -> Dict[str, Any]:
        if path and path.exists():
            with open(path, "rb") as f:
                return tomllib.load(f)
        return {}


# ══════════════════════════════════════════════════════════════════════
# AgentSettings (1.3.1 — Refactored, 1.3.5 — Validation)
# ══════════════════════════════════════════════════════════════════════


class AgentSettings(BaseSettings):
    """The single source of truth for system configuration.

    Every configurable value — model selection, iteration limits, token
    budgets, tool safety toggles, provider endpoints — lives here
    with Pydantic validation.
    """

    # ── LLM Provider Settings ────────────────────────────────────

    default_model: str = Field(
        default="gemini-2.5-flash-lite",
        description="The default LLM model to use for agent tasks.",
    )
    providers: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw provider configurations from TOML (e.g. [providers.ollama]).",
    )
    core: Dict[str, Any] = Field(
        default_factory=dict,
        description="Core framework configuration overrides.",
    )

    # ── Agent Reasoning ──────────────────────────────────────────

    max_iterations: int = Field(
        default=100,
        ge=1,
        le=5000,
        description="Maximum ReAct loop iterations per task.",
    )
    max_task_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description="How many times to retry a failed task before giving up.",
    )
    default_effort: str = Field(
        default=EffortLevel.AUTO.value,
        description=(
            "Default reasoning effort for providers that support thinking levels."
        ),
    )

    # ── Memory & Context ─────────────────────────────────────────

    semantic_memory_enabled: bool = Field(
        default=True,
        description="Enable Mem0 semantic memory for cross-session learning.",
    )
    semantic_memory_auto_learn: bool = Field(
        default=True,
        description="Auto-summarize and store facts after every successful task.",
    )

    # ── Tool Execution ───────────────────────────────────────────

    tool_output_max_chars: int = Field(
        default=5000,
        ge=500,
        le=50000,
        description="Max characters in a tool output before truncation.",
    )
    workspace_deny_patterns: List[str] = Field(
        default_factory=lambda: [".env", ".git/", "*.pem", "*.key"],
        description="Glob-like patterns denied by strict workspace policy.",
    )
    workspace_allow_overrides: List[str] = Field(
        default_factory=list,
        description="Patterns that override workspace deny patterns.",
    )
    disabled_tools: List[str] = Field(
        default_factory=list,
        description="Tool names to disable (e.g. ['spawn_terminal']).",
    )

    # ── Human-in-the-Loop ────────────────────────────────────────

    auto_approve_tools: bool = Field(
        default=False,
        description="If True, skip ALL tool approval prompts. USE WITH CAUTION.",
    )
    auto_approve_safe_commands: bool = Field(
        default=True,
        description="Auto-approve shell commands matching safe regex patterns.",
    )
    approval_timeout_seconds: int = Field(
        default=0,
        ge=0,
        description="Auto-deny after N seconds. 0 = wait forever.",
    )

    # ── UI / TUI ─────────────────────────────────────────────────

    show_agent_thinking: bool = Field(
        default=True,
        description="Show agent reasoning monologue in the TUI.",
    )
    default_agent: str = Field(
        default="default",
        description="Agent name activated by default on session start.",
    )
    agents: Dict[str, Any] = Field(
        default_factory=dict,
        description="User-defined agents from [agents.*] TOML sections.",
    )

    # ── Observability ────────────────────────────────────────────

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Minimum log level for structured JSON logging.",
    )
    log_max_file_size_mb: int = Field(
        default=50,
        ge=1,
        le=1024,
        description="Max JSONL log file size (MB) before rotation.",
    )
    log_directory: str = Field(
        default="~/.agent_cli/logs",
        description="Directory for structured log files.",
    )

    # ── Session Persistence ──────────────────────────────────────

    session_auto_save: bool = Field(
        default=True,
        description="Auto-save session after every message and state transition.",
    )
    session_retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Sessions older than this are auto-pruned.",
    )

    # ── API Keys (1.3.4 — from env / .env / keyring — NEVER TOML) ──

    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    azure_openai_api_key: Optional[str] = Field(
        default=None,
        alias="AZURE_OPENAI_API_KEY",
    )
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    huggingface_api_key: Optional[str] = Field(default=None, alias="HF_TOKEN")
    openrouter_api_key: Optional[str] = Field(default=None, alias="OPENROUTER_API_KEY")

    # ── Pydantic Settings Config ─────────────────────────────────

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=(str(Path.home() / ".agent_cli" / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore unknown fields in TOML (forward compat)
        populate_by_name=True,  # Allow both alias and field name
    )

    # ── Validators (1.3.5) ───────────────────────────────────────

    @field_validator("log_directory")
    @classmethod
    def _expand_log_directory(cls, v: str) -> str:
        """Expand ~ in log directory path."""
        return str(Path(v).expanduser())

    @field_validator("core")
    @classmethod
    def _validate_core_overrides(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        """Validate known core overrides while preserving unknown keys."""
        if not isinstance(value, dict):
            return value

        raw_mode = value.get("protocol_mode")
        if raw_mode is not None:
            normalized = str(raw_mode).strip().lower()
            allowed = {mode.value for mode in ProtocolMode}
            if normalized not in allowed:
                allowed_str = ", ".join(sorted(allowed))
                raise ValueError(
                    f"core.protocol_mode must be one of: {allowed_str}. Got: {raw_mode!r}"
                )
            value["protocol_mode"] = normalized

        return value

    @field_validator("default_effort")
    @classmethod
    def _validate_default_effort(cls, value: str) -> str:
        """Normalize and validate the configured default effort value."""
        normalized = str(value).strip().lower()
        allowed = set(effort_values())
        if normalized not in allowed:
            allowed_str = ", ".join(sorted(allowed))
            raise ValueError(
                f"default_effort must be one of: {allowed_str}. Got: {value!r}"
            )
        return normalized

    @property
    def protocol_mode(self) -> ProtocolMode:
        """Resolve protocol mode.

        Precedence:
        1. `core.protocol_mode` (TOML/env-injected mapping)
        2. default: `json_only`
        """
        raw_mode = (
            self.core.get("protocol_mode") if isinstance(self.core, dict) else None
        )
        if raw_mode:
            return ProtocolMode(str(raw_mode).strip().lower())
        return ProtocolMode.JSON_ONLY

    # ── Runtime Lookups (API Keys) ────────────────────────────────

    def resolve_api_key(self, provider: str) -> Optional[str]:
        """Resolve API key with fallback chain: env/.env → keyring → None."""
        key_map = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "azure": self.azure_openai_api_key,
            "google": self.google_api_key,
            "huggingface": self.huggingface_api_key,
            "openrouter": self.openrouter_api_key,
        }
        key = key_map.get(provider)
        if key:
            return key

        # Fallback: OS keyring
        try:
            import keyring

            return keyring.get_password("agent-cli", f"{provider}_api_key")
        except Exception:
            return None

    # ── Source Priority Override (1.3.2) ──────────────────────────

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Override source priority.

        Highest → Lowest:
        1. init_settings        (CLI flags, passed at instantiation)
        2. env_settings         (AGENT_* environment variables)
        3. dotenv_settings      (.env file)
        4. TomlConfigSettings   (Global + Local TOML merged)
        5. Field defaults       (implicit, lowest)
        """
        global_path = Path.home() / ".agent_cli" / "config.toml"
        local_path = _find_workspace_config()

        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, global_path, local_path),
        )


# ══════════════════════════════════════════════════════════════════════
# Provider Loading (1.3.3)
# ══════════════════════════════════════════════════════════════════════


def load_providers(
    config_data: Dict[str, Any] | None = None,
    *,
    data_registry: DataRegistry | None = None,
) -> Dict[str, ProviderConfig]:
    """Parse provider definitions from merged TOML config.

    Built-in providers are always registered.  Custom providers
    from the ``[providers.*]`` TOML sections extend the list.
    """
    registry = data_registry or DataRegistry()
    providers = registry.get_builtin_providers()

    if config_data is None:
        return providers

    for name, pdata in config_data.get("providers", {}).items():
        existing = providers.get(name)

        # Merge logic: Use existing value as default if user didn't override it in TOML
        default_adapter = (
            existing.adapter_type if existing is not None else "openai_compatible"
        )
        default_url = existing.base_url if existing is not None else None
        default_key_env = existing.api_key_env if existing is not None else None
        default_model = existing.default_model if existing is not None else None
        default_max_ctx = existing.max_context_tokens if existing is not None else None
        default_native_tools = (
            existing.supports_native_tools if existing is not None else False
        )

        providers[name] = ProviderConfig(
            adapter_type=pdata.get("adapter_type", default_adapter),
            base_url=pdata.get("base_url", default_url),
            api_key_env=pdata.get("api_key_env", default_key_env),
            default_model=pdata.get("default_model", default_model),
            supports_native_tools=pdata.get(
                "supports_native_tools",
                default_native_tools,
            ),
            max_context_tokens=pdata.get("max_context_tokens", default_max_ctx),
        )

    return providers


# ══════════════════════════════════════════════════════════════════════
# First-Run & Helpers
# ══════════════════════════════════════════════════════════════════════


_DEFAULT_CONFIG_CONTENT = """\
# Agent CLI Configuration
# See documentation for all available options.

default_model = "gemini-2.5-flash-lite"
default_agent = "default"
max_iterations = 100
default_effort = "auto"
show_agent_thinking = true
log_level = "INFO"
log_max_file_size_mb = 50
session_retention_days = 30

[core]
protocol_mode = "json_only"
"""


def ensure_global_config(global_path: Path | None = None) -> Path:
    """Create default global config on first run.  Returns the path."""
    if global_path is None:
        global_path = Path.home() / ".agent_cli" / "config.toml"

    if global_path.exists():
        return global_path

    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(_DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    logger.info("Created default config at %s", global_path)
    return global_path


def _find_workspace_config() -> Path | None:
    """Walk up from CWD to find ``.agent_cli/settings.toml``."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / ".agent_cli" / "settings.toml"
        if candidate.exists():
            return candidate
    return None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *override* into *base* (override wins)."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
