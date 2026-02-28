# Configuration Management Architecture

## Overview
Every configurable value in the system — model selection, effort levels, token budgets, tool safety toggles, provider endpoints — must be externalized, validated, and overridable. This spec is the **single source of truth** for how configuration is loaded, merged, validated, and mutated at runtime.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Validation** | Pydantic `BaseSettings` | Type safety, env var merging, `.env` file support, helpful error messages |
| **File Format** | TOML | Human-readable, supports nested sections, Python ecosystem standard |
| **Merge Strategy** | Tri-Layer: Factory Defaults → Global TOML → Local TOML → Env/CLI | Sensible override chain. Project-specific overrides global. Env vars override everything. |
| **Runtime Mutation** | Write back to Global TOML | `/config set` persists across sessions. Users can inspect the file. |
| **Provider Registration** | TOML-based with adapter types | Users add custom providers without code changes. |
| **Secrets** | Never in TOML. Env vars / `.env` / OS Keyring only. | Security. See Section 8. |

---

## 2. The Configuration Hierarchy

Configurations resolve from multiple sources, with higher precedence overriding lower:

```
Priority (lowest → highest):

1. Factory Defaults       Code-level defaults in AgentSettings fields
        ↑
2. Global Config          ~/.agent_cli/config.toml
        ↑
3. Local Workspace Config .agent_cli/settings.toml (in project root)
        ↑
4. Environment Variables  AGENT_DEFAULT_MODEL=gpt-4o
        ↑
5. CLI Flags              agent run "Fix bugs" --effort HIGH
```

### File Locations

| Config | Path | Scope |
|---|---|---|
| Global Config | `~/.agent_cli/config.toml` | All projects, all workspaces |
| Local Workspace Config | `<project_root>/.agent_cli/settings.toml` | This project only |
| Local Environment | `<project_root>/.env` | Secrets for this project (`.gitignore`-d) |
| Session Database | `~/.agent_cli/sessions.db` | All sessions (see `04_session_persistence.md`) |
| Log Files | `~/.agent_cli/logs/` | All logs (see `03_observability.md`) |

---

## 3. The Complete `AgentSettings` Class

This consolidates **every configurable value** from all architecture specs:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Dict, List, Literal, Optional
from enum import Enum


class EffortLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AgentSettings(BaseSettings):
    """
    The Single Source of Truth for system configuration.
    
    Loading order (lowest → highest precedence):
    1. Field defaults (below)
    2. Global TOML (~/.agent_cli/config.toml)
    3. Local TOML (.agent_cli/settings.toml)
    4. .env file
    5. Environment variables (AGENT_ prefix)
    6. CLI flags (injected at startup)
    """
    
    # ── LLM Provider Settings ────────────────────────────────
    
    default_model: str = Field(
        default="claude-3-5-sonnet",
        description="The default LLM model to use for agent tasks."
    )
    routing_model: str = Field(
        default="claude-3-5-haiku",
        description="Fast/cheap model used for routing classification."
    )
    summarization_model: str = Field(
        default="claude-3-5-haiku",
        description="Fast/cheap model used for context summarization."
    )
    
    # ── Agent Reasoning ──────────────────────────────────────
    
    default_effort_level: EffortLevel = Field(
        default=EffortLevel.MEDIUM,
        description="Default effort level for agents (overridable per-task and per-agent)."
    )
    max_task_retries: int = Field(
        default=1, ge=0, le=5,
        description="How many times to retry a failed task before giving up."
    )
    
    # ── Memory & Context ─────────────────────────────────────
    
    context_budget_system_prompt_pct: float = Field(
        default=0.15, ge=0.05, le=0.50,
        description="Percentage of context window allocated to system prompt."
    )
    context_budget_summary_pct: float = Field(
        default=0.10, ge=0.0, le=0.30,
        description="Percentage allocated to compacted summary block."
    )
    context_budget_response_reserve_pct: float = Field(
        default=0.20, ge=0.10, le=0.40,
        description="Percentage reserved for LLM response generation."
    )
    context_compaction_threshold: float = Field(
        default=0.80, ge=0.50, le=0.95,
        description="Trigger summarization when working memory exceeds this % of budget."
    )
    semantic_memory_enabled: bool = Field(
        default=True,
        description="Enable Mem0 semantic memory for cross-session learning."
    )
    semantic_memory_auto_learn: bool = Field(
        default=True,
        description="Auto-summarize and store facts after every successful task."
    )
    
    # ── Tool Execution ───────────────────────────────────────
    
    tool_output_max_chars: int = Field(
        default=5000, ge=500, le=50000,
        description="Max characters in a tool output before truncation."
    )
    terminal_max_lines: int = Field(
        default=2000, ge=100, le=50000,
        description="Max lines kept in RAM per persistent terminal."
    )
    disabled_tools: List[str] = Field(
        default_factory=list,
        description="Tool names to disable (e.g., ['spawn_terminal', 'run_command'])."
    )
    
    # ── Human-in-the-Loop ────────────────────────────────────
    
    auto_approve_tools: bool = Field(
        default=False,
        description="If True, skip ALL tool approval prompts. USE WITH CAUTION."
    )
    auto_approve_safe_commands: bool = Field(
        default=True,
        description="Auto-approve shell commands matching safe regex patterns."
    )
    approval_timeout_seconds: int = Field(
        default=0, ge=0,
        description="Auto-deny after N seconds. 0 = wait forever."
    )
    
    # ── UI / TUI ─────────────────────────────────────────────
    
    show_agent_thinking: bool = Field(
        default=True,
        description="Show agent's <thinking> monologue in the TUI."
    )
    
    # ── Observability ────────────────────────────────────────
    
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Minimum log level for structured JSON logging."
    )
    log_directory: str = Field(
        default="~/.agent_cli/logs",
        description="Directory for structured log files."
    )
    
    # ── Session Persistence ──────────────────────────────────
    
    session_auto_save: bool = Field(
        default=True,
        description="Auto-save session after every message and state transition."
    )
    session_retention_days: int = Field(
        default=30, ge=1, le=365,
        description="Sessions older than this are auto-pruned."
    )
    
    # ── API Keys (from env / .env / keyring — NEVER TOML) ────
    
    anthropic_api_key: Optional[str] = Field(
        default=None, alias="ANTHROPIC_API_KEY"
    )
    openai_api_key: Optional[str] = Field(
        default=None, alias="OPENAI_API_KEY"
    )
    google_api_key: Optional[str] = Field(
        default=None, alias="GOOGLE_API_KEY"
    )
    
    # ── Pydantic Settings Config ─────────────────────────────
    
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file=[],  # Populated dynamically by the custom loader
        extra="ignore"  # Ignore unknown fields in TOML (forward compatibility)
    )
    
    # ── API Key Resolution ───────────────────────────────────
    
    def resolve_api_key(self, provider: str) -> Optional[str]:
        """
        Resolve API key with fallback to OS keyring.
        Priority: env var / .env → keyring → None
        """
        key_map = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "google": self.google_api_key,
        }
        key = key_map.get(provider)
        
        if key:
            return key
        
        # Fallback: try OS keyring
        try:
            import keyring
            return keyring.get_password("agent-cli", f"{provider}_api_key")
        except Exception:
            return None
```

---

## 4. TOML File Format

### Global Config (`~/.agent_cli/config.toml`)

```toml
# ── Agent CLI Global Configuration ──────────────────────────

# LLM Provider Settings
default_model = "claude-3-5-sonnet"
routing_model = "claude-3-5-haiku"
summarization_model = "claude-3-5-haiku"

# Agent Defaults
default_effort_level = "MEDIUM"
max_task_retries = 1

# Memory & Context
context_compaction_threshold = 0.80
semantic_memory_enabled = true
semantic_memory_auto_learn = true

# Tool Execution
tool_output_max_chars = 5000
terminal_max_lines = 2000

# Human-in-the-Loop
auto_approve_tools = false
auto_approve_safe_commands = true

# UI
show_agent_thinking = true

# Logging
log_level = "INFO"

# Sessions
session_retention_days = 30

# ── Provider Definitions ────────────────────────────────────
# Built-in providers are auto-registered. Add custom ones here.

[providers.local_ollama]
adapter_type = "openai_compatible"
base_url = "http://localhost:11434/v1"
models = ["llama-3-8b", "codestral"]

[providers.lmstudio]
adapter_type = "openai_compatible"
base_url = "http://localhost:1234/v1"
models = ["deepseek-coder-v2"]

# ── User-Defined Agents ─────────────────────────────────────
# See 01_reasoning_loop.md Section 8 for full format.

[agents.devops]
description = "Infrastructure and deployment specialist"
capabilities = ["infrastructure", "debugging"]
persona = "You are a DevOps engineer specializing in Docker and CI/CD."
model = "claude-3-5-sonnet"
effort_level = "HIGH"
tools = ["read_file", "write_file", "run_command", "spawn_terminal"]
show_thinking = true

[agents.reviewer]
description = "Code review specialist"
capabilities = ["code_review", "research"]
persona = "You are a senior code reviewer focused on security and performance."
model = "gpt-4o"
effort_level = "MEDIUM"
tools = ["read_file", "grep_search", "find_files"]
show_thinking = false
```

### Local Workspace Config (`<project>/.agent_cli/settings.toml`)

```toml
# ── Project-Specific Overrides ──────────────────────────────
# These override the global config for this workspace only.

default_model = "gpt-4o"           # This project uses OpenAI
default_effort_level = "HIGH"      # Complex project, always use deep reasoning
disabled_tools = ["spawn_terminal"] # No background processes in this project
tool_output_max_chars = 10000      # Larger outputs needed for this codebase

# Project-specific agent override
[agents.tester]
description = "Test specialist for this project"
capabilities = ["testing", "debugging"]
persona = "You are a testing expert. This project uses pytest with fixtures."
tools = ["read_file", "run_command", "grep_search"]
effort_level = "MEDIUM"
```

---

## 5. Custom TOML Loader (Tri-Layer Merge)

Pydantic `BaseSettings` handles env vars and `.env` natively, but does NOT merge multiple TOML files. We implement a custom settings source:

```python
import tomllib
from pathlib import Path
from typing import Any, Dict, Tuple, Type
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
)


class TomlConfigSettingsSource(PydanticBaseSettingsSource):
    """
    Custom Pydantic settings source that loads and merges
    Global TOML + Local Workspace TOML.
    
    Merge order: Global values are loaded first, then Local values
    overwrite any duplicates.
    """
    
    def __init__(
        self,
        settings_cls: Type[BaseSettings],
        global_path: Path,
        local_path: Path | None = None,
    ):
        super().__init__(settings_cls)
        self.global_path = global_path
        self.local_path = local_path
    
    def _load_toml(self, path: Path) -> Dict[str, Any]:
        if path.exists():
            with open(path, "rb") as f:
                return tomllib.load(f)
        return {}
    
    def get_field_value(
        self, field, field_name: str
    ) -> Tuple[Any, str, bool]:
        # Merge: global first, local overwrites
        global_data = self._load_toml(self.global_path)
        local_data = self._load_toml(self.local_path) if self.local_path else {}
        
        merged = {**global_data, **local_data}
        
        val = merged.get(field_name)
        return val, field_name, val is not None
    
    def __call__(self) -> Dict[str, Any]:
        global_data = self._load_toml(self.global_path)
        local_data = self._load_toml(self.local_path) if self.local_path else {}
        
        # Deep merge: local values override global
        merged = self._deep_merge(global_data, local_data)
        return merged
    
    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Recursively merge override into base."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = TomlConfigSettingsSource._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


class AgentSettings(BaseSettings):
    # ... all fields from Section 3 ...
    
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        """
        Override source priority:
        1. init_settings (CLI flags, passed at instantiation)  ← Highest
        2. env_settings (AGENT_* environment variables)
        3. dotenv_settings (.env file)
        4. TomlConfigSettingsSource (Global + Local TOML)
        5. Field defaults                                      ← Lowest
        """
        global_path = Path.home() / ".agent_cli" / "config.toml"
        local_path = _find_workspace_config()
        
        return (
            init_settings,          # CLI flags (highest)
            env_settings,           # AGENT_* env vars
            dotenv_settings,        # .env file
            TomlConfigSettingsSource(settings_cls, global_path, local_path),
            # Field defaults are implicit (lowest)
        )


def _find_workspace_config() -> Path | None:
    """
    Walk up from CWD to find .agent_cli/settings.toml.
    Returns None if not found.
    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / ".agent_cli" / "settings.toml"
        if candidate.exists():
            return candidate
    return None
```

---

## 6. Provider Registration via TOML

Users can add custom LLM providers without code changes. The system supports four built-in adapter types:

| Adapter Type | Built-in For | Custom Use |
|---|---|---|
| `openai` | OpenAI models | — |
| `anthropic` | Claude models | — |
| `google` | Gemini / Vertex | — |
| `openai_compatible` | — | Ollama, LM Studio, vLLM, any OpenAI-compatible API |

### Provider Config Schema

```python
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    adapter_type: str                          # "openai", "anthropic", "google", "openai_compatible"
    base_url: Optional[str] = None             # Custom endpoint (required for openai_compatible)
    models: List[str] = field(default_factory=list)  # Available models
    api_key_env: Optional[str] = None          # Custom env var name for API key
    default_model: Optional[str] = None        # Default model for this provider
    supports_native_tools: bool = True         # Whether this provider supports native FC
    max_context_tokens: Optional[int] = None   # Override default context window size
```

### Loading Providers

```python
def load_providers(config_data: dict) -> Dict[str, ProviderConfig]:
    """
    Parse provider definitions from merged TOML config.
    Built-in providers are always registered. Custom providers extend the list.
    """
    providers = {}
    
    # Built-in providers (always available, API key from env/keyring)
    providers["openai"] = ProviderConfig(
        adapter_type="openai",
        models=["gpt-4o", "gpt-4o-mini", "o1", "o1-mini"],
        default_model="gpt-4o"
    )
    providers["anthropic"] = ProviderConfig(
        adapter_type="anthropic",
        models=["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229"],
        default_model="claude-3-5-sonnet-20241022"
    )
    providers["google"] = ProviderConfig(
        adapter_type="google",
        models=["gemini-2.0-flash", "gemini-2.0-pro"],
        default_model="gemini-2.0-flash"
    )
    
    # Custom providers from TOML
    for name, provider_data in config_data.get("providers", {}).items():
        providers[name] = ProviderConfig(
            adapter_type=provider_data.get("adapter_type", "openai_compatible"),
            base_url=provider_data.get("base_url"),
            models=provider_data.get("models", []),
            api_key_env=provider_data.get("api_key_env"),
            default_model=provider_data.get("default_model"),
            supports_native_tools=provider_data.get("supports_native_tools", False),
            max_context_tokens=provider_data.get("max_context_tokens"),
        )
    
    return providers
```

### CLI Provider Management

```bash
# Add a new provider via CLI
agent config add-provider local_ollama \
    --type openai_compatible \
    --url http://localhost:11434/v1 \
    --models llama-3-8b,codestral

# List configured providers
agent config providers

# Test a provider connection
agent config test-provider local_ollama
```

---

## 7. Runtime Config Mutation (`/config` Commands)

The TUI supports viewing and modifying config at runtime:

```python
class ConfigCommand:
    """Handles /config commands in the TUI."""
    
    def __init__(self, settings: AgentSettings, global_config_path: Path):
        self.settings = settings
        self.config_path = global_config_path
    
    def execute(self, args: List[str]) -> str:
        if not args:
            return self._show_all()
        
        subcmd = args[0]
        
        if subcmd == "show":
            return self._show_all()
        elif subcmd == "get" and len(args) >= 2:
            return self._get(args[1])
        elif subcmd == "set" and len(args) >= 2:
            return self._set(args[1])
        elif subcmd == "providers":
            return self._list_providers()
        elif subcmd == "reset":
            return self._reset(args[1] if len(args) >= 2 else None)
        else:
            return "Usage: /config [show|get <key>|set <key>=<value>|providers|reset [key]]"
    
    def _set(self, key_value: str) -> str:
        """
        Set a config value. Updates in-memory AND writes to Global TOML.
        Example: /config set default_effort_level=HIGH
        """
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        
        if not hasattr(self.settings, key):
            return f"Unknown config key: '{key}'"
        
        # Type coercion based on field type
        field_info = self.settings.model_fields.get(key)
        if not field_info:
            return f"Unknown config key: '{key}'"
        
        try:
            coerced = self._coerce_value(value, field_info.annotation)
            setattr(self.settings, key, coerced)
            self._write_to_toml(key, coerced)
            return f"✓ Set {key} = {coerced}"
        except (ValueError, TypeError) as e:
            return f"Invalid value for '{key}': {e}"
    
    def _write_to_toml(self, key: str, value: Any) -> None:
        """Write a single key-value update to the Global TOML file."""
        import tomli_w
        
        # Load existing TOML
        data = {}
        if self.config_path.exists():
            with open(self.config_path, "rb") as f:
                data = tomllib.load(f)
        
        # Update the value
        data[key] = value
        
        # Write back
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "wb") as f:
            tomli_w.dump(data, f)
    
    def _show_all(self) -> str:
        """Display all current config values."""
        lines = ["Current Configuration:\n"]
        for key, field_info in self.settings.model_fields.items():
            if "api_key" in key:
                val = "****" if getattr(self.settings, key) else "Not set"
            else:
                val = getattr(self.settings, key)
            lines.append(f"  {key} = {val}")
        return "\n".join(lines)
```

### TUI Commands

```
/config show                          Show all current settings
/config get default_model             Show a single setting
/config set default_model=gpt-4o      Change a setting (persists to TOML)
/config set default_effort_level=HIGH
/config providers                     List configured LLM providers
/config reset default_model           Reset a setting to factory default
/config reset                         Reset ALL settings to factory defaults
```

---

## 8. Secrets Management (API Keys & Sensitive Data)

### The Secrets Hierarchy (Strict Rules)

| Priority | Source | Scope | Security | Example |
|---|---|---|---|---|
| 1 (Lowest) | Factory Defaults | — | N/A | `api_key = None` |
| 2 | `.env` File (project local) | Per-project | Medium (`.gitignore`-able) | `ANTHROPIC_API_KEY=sk-ant-...` |
| 3 | OS Environment Variables | Global | Medium (no file to leak) | `export OPENAI_API_KEY=sk-...` |
| 4 (Highest) | OS Keyring | Global | High (encrypted storage) | `keyring.set_password(...)` |

### Rules
1. **NEVER in TOML:** Config files must *never* store API keys.
2. **`.env` File:** For project-specific tokens. Read by `pydantic-settings`. Must be in `.gitignore`.
3. **OS Environment Variables:** Standard approach (`export ANTHROPIC_API_KEY="..."`).
4. **OS Keyring:** Uses Python's `keyring` library for macOS Keychain, Windows Credential Locker, or Linux Secret Service.

### Keyring Management via CLI

```bash
agent auth set openai        # Prompts for key input (masked)
agent auth set anthropic
agent auth status            # Shows which providers have keys (redacted)
agent auth remove openai
```

### Sanitization Cross-Reference

All log sanitization is specified in `03_observability.md` Section 7.

---

## 9. First-Run Experience

On first launch, if no global config exists, the system:

```python
def ensure_config_exists(global_path: Path) -> None:
    """Create default config on first run."""
    if global_path.exists():
        return
    
    global_path.parent.mkdir(parents=True, exist_ok=True)
    
    default_config = """\
# Agent CLI Configuration
# See documentation for all available options.

default_model = "claude-3-5-sonnet"
default_effort_level = "MEDIUM"
show_agent_thinking = true
log_level = "INFO"
session_retention_days = 30
"""
    
    global_path.write_text(default_config, encoding="utf-8")
    print(f"Created default config at {global_path}")
```

---

## 10. Testing Strategy

```python
import pytest
from pathlib import Path
import tempfile

def test_factory_defaults():
    """Settings should have sensible defaults with no config files."""
    settings = AgentSettings()
    assert settings.default_model == "claude-3-5-sonnet"
    assert settings.default_effort_level == EffortLevel.MEDIUM
    assert settings.auto_approve_tools == False

def test_global_toml_overrides_defaults(tmp_path):
    """Global TOML values should override factory defaults."""
    config = tmp_path / "config.toml"
    config.write_text('default_model = "gpt-4o"\ndefault_effort_level = "HIGH"')
    
    settings = AgentSettings(_toml_global=config)
    assert settings.default_model == "gpt-4o"
    assert settings.default_effort_level == EffortLevel.HIGH

def test_local_toml_overrides_global(tmp_path):
    """Local workspace TOML should override global TOML."""
    global_config = tmp_path / "global.toml"
    global_config.write_text('default_model = "gpt-4o"')
    
    local_config = tmp_path / "local.toml"
    local_config.write_text('default_model = "claude-3-5-sonnet"')
    
    source = TomlConfigSettingsSource(AgentSettings, global_config, local_config)
    merged = source()
    assert merged["default_model"] == "claude-3-5-sonnet"

def test_env_var_overrides_toml():
    """Environment variables should override TOML values."""
    import os
    os.environ["AGENT_DEFAULT_MODEL"] = "o1"
    
    settings = AgentSettings()
    assert settings.default_model == "o1"
    
    del os.environ["AGENT_DEFAULT_MODEL"]

def test_api_keys_never_in_toml(tmp_path):
    """API keys in TOML should be ignored (security enforcement)."""
    config = tmp_path / "config.toml"
    config.write_text('openai_api_key = "sk-stolen"')
    
    # API keys should only come from env/keyring, not TOML
    settings = AgentSettings(_toml_global=config)
    # The TOML loader should explicitly skip api_key fields

def test_deep_merge_preserves_nested():
    """Deep merge should handle nested dicts (agent configs)."""
    base = {"agents": {"coder": {"effort_level": "LOW", "model": "haiku"}}}
    override = {"agents": {"coder": {"effort_level": "HIGH"}}}
    
    result = TomlConfigSettingsSource._deep_merge(base, override)
    assert result["agents"]["coder"]["effort_level"] == "HIGH"
    assert result["agents"]["coder"]["model"] == "haiku"  # Preserved

def test_provider_loading():
    """Custom providers should be loadable from TOML."""
    data = {
        "providers": {
            "local_ollama": {
                "adapter_type": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "models": ["llama-3-8b"]
            }
        }
    }
    providers = load_providers(data)
    
    assert "local_ollama" in providers
    assert providers["local_ollama"].base_url == "http://localhost:11434/v1"
    assert "openai" in providers  # Built-in always present

def test_config_set_persists(tmp_path):
    """Runtime /config set should update both memory and TOML file."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_model = "gpt-4o"')
    
    settings = AgentSettings()
    cmd = ConfigCommand(settings, config_path)
    
    result = cmd._set("default_effort_level=HIGH")
    assert "✓" in result
    assert settings.default_effort_level == EffortLevel.HIGH
    
    # Verify persisted to file
    with open(config_path, "rb") as f:
        saved = tomllib.load(f)
    assert saved["default_effort_level"] == "HIGH"

def test_invalid_config_value():
    """Invalid values should produce helpful error messages."""
    settings = AgentSettings()
    cmd = ConfigCommand(settings, Path("/tmp/config.toml"))
    
    result = cmd._set("max_task_retries=not_a_number")
    assert "Invalid" in result
```
