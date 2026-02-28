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
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from agent_cli.core.models.config_models import EffortLevel, ProviderConfig

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

    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
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
            local_data = (
                self._load_toml(self._local_path) if self._local_path else {}
            )
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

    Every configurable value — model selection, effort levels, token
    budgets, tool safety toggles, provider endpoints — lives here
    with Pydantic validation.
    """

    # ── LLM Provider Settings ────────────────────────────────────

    default_model: str = Field(
        default="claude-3-5-sonnet",
        description="The default LLM model to use for agent tasks.",
    )
    routing_model: str = Field(
        default="claude-3-5-haiku",
        description="Fast/cheap model used for routing classification.",
    )
    summarization_model: str = Field(
        default="claude-3-5-haiku",
        description="Fast/cheap model used for context summarization.",
    )

    # ── Agent Reasoning ──────────────────────────────────────────

    default_effort_level: EffortLevel = Field(
        default=EffortLevel.MEDIUM,
        description="Default effort level for agents (overridable per-task).",
    )
    max_task_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description="How many times to retry a failed task before giving up.",
    )

    # ── Memory & Context ─────────────────────────────────────────

    context_budget_system_prompt_pct: float = Field(
        default=0.15,
        ge=0.05,
        le=0.50,
        description="Percentage of context window allocated to system prompt.",
    )
    context_budget_summary_pct: float = Field(
        default=0.10,
        ge=0.0,
        le=0.30,
        description="Percentage allocated to compacted summary block.",
    )
    context_budget_response_reserve_pct: float = Field(
        default=0.20,
        ge=0.10,
        le=0.40,
        description="Percentage reserved for LLM response generation.",
    )
    context_compaction_threshold: float = Field(
        default=0.80,
        ge=0.50,
        le=0.95,
        description="Trigger summarization when working memory exceeds this % of budget.",
    )
    semantic_memory_enabled: bool = Field(
        default=True,
        description="Enable Mem0 semantic memory for cross-session learning.",
    )
    semantic_memory_auto_learn: bool = Field(
        default=True,
        description="Auto-summarize and store facts after every successful task.",
    )

    # ── Retry Settings ───────────────────────────────────────────

    llm_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts for transient LLM API errors.",
    )
    llm_retry_base_delay: float = Field(
        default=1.0,
        ge=0.1,
        description="Base delay (seconds) for exponential backoff.",
    )
    llm_retry_max_delay: float = Field(
        default=30.0,
        description="Maximum delay cap (seconds) for exponential backoff.",
    )
    max_consecutive_schema_errors: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max consecutive malformed LLM responses before failing.",
    )

    # ── Tool Execution ───────────────────────────────────────────

    tool_output_max_chars: int = Field(
        default=5000,
        ge=500,
        le=50000,
        description="Max characters in a tool output before truncation.",
    )
    terminal_max_lines: int = Field(
        default=2000,
        ge=100,
        le=50000,
        description="Max lines kept in RAM per persistent terminal.",
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
        description="Show agent's <thinking> monologue in the TUI.",
    )

    # ── Observability ────────────────────────────────────────────

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Minimum log level for structured JSON logging.",
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

    anthropic_api_key: Optional[str] = Field(
        default=None, alias="ANTHROPIC_API_KEY"
    )
    openai_api_key: Optional[str] = Field(
        default=None, alias="OPENAI_API_KEY"
    )
    google_api_key: Optional[str] = Field(
        default=None, alias="GOOGLE_API_KEY"
    )

    # ── Pydantic Settings Config ─────────────────────────────────

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore unknown fields in TOML (forward compat)
        populate_by_name=True,  # Allow both alias and field name
    )

    # ── Validators (1.3.5) ───────────────────────────────────────

    @field_validator("context_budget_system_prompt_pct", "context_budget_summary_pct", "context_budget_response_reserve_pct")
    @classmethod
    def _budget_percentages_sanity(cls, v: float) -> float:
        """Ensure budget percentages are fractions, not whole numbers."""
        if v > 1.0:
            raise ValueError(
                f"Budget percentage must be 0.0–1.0, got {v}. "
                "Use 0.15 instead of 15."
            )
        return v

    @field_validator("log_directory")
    @classmethod
    def _expand_log_directory(cls, v: str) -> str:
        """Expand ~ in log directory path."""
        return str(Path(v).expanduser())

    # ── API Key Resolution (1.3.4) ───────────────────────────────

    def resolve_api_key(self, provider: str) -> Optional[str]:
        """Resolve API key with fallback chain: env/.env → keyring → None."""
        key_map = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "google": self.google_api_key,
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


# Built-in providers (always available — API key from env/keyring)
_BUILTIN_PROVIDERS: Dict[str, ProviderConfig] = {
    "openai": ProviderConfig(
        adapter_type="openai",
        models=["gpt-4o", "gpt-4o-mini", "o1", "o1-mini"],
        default_model="gpt-4o",
    ),
    "anthropic": ProviderConfig(
        adapter_type="anthropic",
        models=[
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        default_model="claude-3-5-sonnet-20241022",
    ),
    "google": ProviderConfig(
        adapter_type="google",
        models=["gemini-2.0-flash", "gemini-2.0-pro"],
        default_model="gemini-2.0-flash",
    ),
}


def load_providers(
    config_data: Dict[str, Any] | None = None,
) -> Dict[str, ProviderConfig]:
    """Parse provider definitions from merged TOML config.

    Built-in providers are always registered.  Custom providers
    from the ``[providers.*]`` TOML sections extend the list.
    """
    providers = dict(_BUILTIN_PROVIDERS)

    if config_data is None:
        return providers

    for name, pdata in config_data.get("providers", {}).items():
        providers[name] = ProviderConfig(
            adapter_type=pdata.get("adapter_type", "openai_compatible"),
            base_url=pdata.get("base_url"),
            models=pdata.get("models", []),
            api_key_env=pdata.get("api_key_env"),
            default_model=pdata.get("default_model"),
            supports_native_tools=pdata.get("supports_native_tools", False),
            max_context_tokens=pdata.get("max_context_tokens"),
        )

    return providers


# ══════════════════════════════════════════════════════════════════════
# First-Run & Helpers
# ══════════════════════════════════════════════════════════════════════


_DEFAULT_CONFIG_CONTENT = """\
# Agent CLI Configuration
# See documentation for all available options.

default_model = "claude-3-5-sonnet"
default_effort_level = "MEDIUM"
show_agent_thinking = true
log_level = "INFO"
session_retention_days = 30
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
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
