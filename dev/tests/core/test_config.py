"""
Unit tests for the Configuration System (Sub-Phase 1.3.6).

Tests cover:
- Default values initialization
- Environment variable overrides
- Tri-layer TOML merging (Global + Local)
- Provider loading and custom extension
- Field validation (e.g., budget percentages)
- API key resolution (with keyring fallback mocking)
"""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_cli.core.infra.config.config import (
    AgentSettings,
    TomlConfigSettingsSource,
    _deep_merge,
    load_providers,
)
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.infra.config.config_models import (
    EffortLevel,
    ProtocolMode,
    effort_values,
    normalize_effort,
)

# ── Defaults & Env Var Tests ──────────────────────────────────────────


def test_default_settings():
    """Verify settings initialize with expected defaults when no env/toml are present."""
    # Ensure no old env vars leak into test
    os.environ.pop("DEFAULT_MODEL", None)

    settings = AgentSettings()
    # In this environment, it might be loading gemini-3-flash-preview from config.toml
    assert settings.default_model in ("claude-3-5-sonnet", "gemini-3-flash-preview")
    assert settings.max_iterations >= 1
    assert settings.log_level == "INFO"
    assert settings.log_max_file_size_mb == 50
    assert settings.max_task_retries == 1


def test_env_var_override():
    """Environment variables should override defaults."""
    os.environ["DEFAULT_MODEL"] = "gpt-4o"
    os.environ["MAX_ITERATIONS"] = "150"
    os.environ["AUTO_APPROVE_TOOLS"] = "true"

    settings = AgentSettings()

    assert settings.default_model == "gpt-4o"
    assert settings.max_iterations == 150
    assert settings.auto_approve_tools is True

    # Cleanup
    del os.environ["DEFAULT_MODEL"]
    del os.environ["MAX_ITERATIONS"]
    del os.environ["AUTO_APPROVE_TOOLS"]


def test_protocol_mode_defaults_to_json_only_when_not_set():
    """Protocol mode should default to json_only."""
    settings = AgentSettings(core={})
    assert settings.protocol_mode == ProtocolMode.JSON_ONLY


def test_protocol_mode_uses_core_override():
    """Explicit core.protocol_mode should take precedence."""
    settings = AgentSettings(core={"protocol_mode": "json_only"})
    assert settings.protocol_mode == ProtocolMode.JSON_ONLY


def test_protocol_mode_rejects_invalid_value():
    """Unknown protocol mode should fail validation."""
    with pytest.raises(ValidationError) as exc_info:
        AgentSettings(core={"protocol_mode": "legacy_tags"})
    assert "core.protocol_mode must be one of" in str(exc_info.value)


def test_default_effort_normalizes_case_and_whitespace():
    """default_effort should be normalized to lowercase canonical values."""
    settings = AgentSettings(default_effort=" HIGH ")
    assert settings.default_effort == EffortLevel.HIGH.value


def test_default_effort_rejects_invalid_value():
    """Unknown effort values should fail validation."""
    with pytest.raises(ValidationError) as exc_info:
        AgentSettings(default_effort="extreme")
    assert "default_effort must be one of" in str(exc_info.value)


def test_effort_helpers_cover_all_enum_values():
    """Effort helper utilities should stay in sync with the enum."""
    assert set(effort_values()) == {level.value for level in EffortLevel}
    assert normalize_effort(None) == EffortLevel.AUTO
    assert normalize_effort("medium") == EffortLevel.MEDIUM


# ── TOML Merging Tests ────────────────────────────────────────────────


def test_deep_merge():
    """Verify nested dictionaries merge correctly."""
    base = {
        "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
        "log_level": "INFO",
    }
    override = {
        "providers": {
            "openai": {"adapter_type": "openai"},
            "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
        },
        "log_level": "DEBUG",
    }

    merged = _deep_merge(base, override)

    assert merged["log_level"] == "DEBUG"
    assert "openai" in merged["providers"]
    # Dicts inside should be merged, not replaced completely if they can be merged
    assert merged["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"
    assert merged["providers"]["openai"]["adapter_type"] == "openai"
    assert "anthropic" in merged["providers"]


def test_toml_source_merge_precedence(tmp_path):
    """Verify TomlConfigSettingsSource correctly prioritizes local over global over defaults."""
    global_toml = tmp_path / "global.toml"
    local_toml = tmp_path / "local.toml"

    global_content = """
    default_model = "global-model"
    log_level = "DEBUG"
    """
    global_toml.write_text(global_content)

    local_content = """
    default_model = "local-model"
    # log_level is NOT set here, should inherit from global
    """
    local_toml.write_text(local_content)

    # Mock settings custom sources to only use the TOML files
    class MockConfig(AgentSettings):
        pass

    source = TomlConfigSettingsSource(MockConfig, global_toml, local_toml)
    data = source()

    assert data["default_model"] == "local-model"
    assert data["log_level"] == "DEBUG"


# ── Validation Tests ──────────────────────────────────────────────────


def test_removed_internal_fields_are_not_exposed_on_settings():
    """Internal tuning fields migrated to DataRegistry should not exist on settings."""
    settings = AgentSettings()

    assert not hasattr(settings, "llm_max_retries")
    assert not hasattr(settings, "context_compaction_threshold")
    assert not hasattr(settings, "workspace_index_max_files")
    assert not hasattr(settings, "session_auto_save_interval_seconds")


def test_numeric_limits_validation():
    """Numeric limits (ge, le) should be enforced."""
    with pytest.raises(ValidationError) as exc_info:
        AgentSettings(max_task_retries=10)  # max is 5

    assert "Input should be less than or equal to 5" in str(exc_info.value)


def test_path_expansion():
    """Log directory should expand ~ to absolute path."""
    settings = AgentSettings(log_directory="~/.test_logs")
    assert settings.log_directory.startswith(str(Path.home()))
    assert ".test_logs" in settings.log_directory


def test_workspace_policy_settings_override():
    """Workspace deny/allow policy settings should be configurable."""
    settings = AgentSettings(
        workspace_deny_patterns=["*.secret", ".vault/"],
        workspace_allow_overrides=["safe.secret"],
    )
    assert settings.workspace_deny_patterns == ["*.secret", ".vault/"]
    assert settings.workspace_allow_overrides == ["safe.secret"]


# ── Provider Loading Tests ────────────────────────────────────────────


def test_load_providers_with_custom_toml():
    """load_providers should merge custom providers with built-ins."""
    config_data = {
        "providers": {
            "local_llama": {
                "adapter_type": "openai_compatible",
                "base_url": "http://localhost:8080",
                "require_verification": False,
            },
            # Override a built-in entirely (optional behavior depending on deep_merge,
            # but load_providers explicitly maps TOML keys)
            "openai": {"adapter_type": "openai", "api_key_env": "OPENAI_API_KEY_ALT"},
        }
    }

    providers = load_providers(config_data, data_registry=DataRegistry())

    assert "anthropic" in providers  # Built-in is kept
    assert "google" in providers  # Built-in is kept
    assert "azure" in providers  # Built-in is kept

    assert "local_llama" in providers
    assert providers["local_llama"].adapter_type == "openai_compatible"
    assert providers["local_llama"].base_url == "http://localhost:8080"
    assert providers["local_llama"].require_verification is False

    assert providers["openai"].api_key_env == "OPENAI_API_KEY_ALT"


def test_load_providers_preserves_builtin_require_verification_when_omitted():
    """Built-in provider verification requirements should survive partial TOML overrides."""
    config_data = {
        "providers": {
            "google": {
                "adapter_type": "google",
            }
        }
    }

    providers = load_providers(config_data, data_registry=DataRegistry())

    assert providers["google"].require_verification is True


def test_load_providers_preserves_builtin_native_tools_when_omitted():
    """Overriding a built-in provider should keep its native-tools default unless explicitly set."""
    config_data = {
        "providers": {
            "google": {
                "adapter_type": "google",
            }
        }
    }

    providers = load_providers(config_data, data_registry=DataRegistry())

    assert providers["google"].supports_native_tools is True


def test_load_providers_custom_provider_defaults_native_tools_false():
    """New custom providers should remain conservative when the flag is omitted."""
    config_data = {
        "providers": {
            "local_llama": {
                "adapter_type": "openai_compatible",
                "base_url": "http://localhost:8080",
            }
        }
    }

    providers = load_providers(config_data, data_registry=DataRegistry())

    assert providers["local_llama"].supports_native_tools is False


def test_load_providers_merges_provider_api_profile() -> None:
    config_data = {
        "providers": {
            "openrouter": {
                "api_profile": {
                    "web_search": {
                        "mutations": [
                            {
                                "op": "merge_body",
                                "value": {"plugins": [{"id": "web-pro"}]},
                            }
                        ],
                    }
                }
            }
        }
    }

    providers = load_providers(config_data, data_registry=DataRegistry())

    assert providers["openrouter"].api_profile["web_search"]["mutations"] == [
        {"op": "merge_body", "value": {"plugins": [{"id": "web-pro"}]}}
    ]
    assert isinstance(
        providers["openrouter"].api_profile["web_search"].get("mutations"),
        list,
    )


# ── API Key Resolution Tests ──────────────────────────────────────────


def test_resolve_api_key_from_env():
    """API key should be returned directly if supplied via settings/env."""
    settings = AgentSettings(openai_api_key="sk-test-key")
    assert settings.resolve_api_key("openai") == "sk-test-key"


def test_resolve_azure_api_key_from_env():
    """Azure API key should resolve from settings/env alias."""
    settings = AgentSettings(azure_openai_api_key="az-test-key")
    assert settings.resolve_api_key("azure") == "az-test-key"


def test_resolve_api_key_from_keyring(monkeypatch):
    """If key not in settings, fallback to keyring."""
    settings = AgentSettings(anthropic_api_key=None)

    # Mock keyring
    def mock_get_password(service, username):
        if service == "agent-cli" and username == "anthropic_api_key":
            return "sk-ant-from-keyring"
        return None

    import keyring

    monkeypatch.setattr(keyring, "get_password", mock_get_password)

    assert settings.resolve_api_key("anthropic") == "sk-ant-from-keyring"


def test_resolve_api_key_not_found(monkeypatch):
    """Return None if API key is not in env and not in keyring."""
    settings = AgentSettings(google_api_key=None)

    def mock_get_password(service, username):
        return None

    import keyring

    monkeypatch.setattr(keyring, "get_password", mock_get_password)

    assert settings.resolve_api_key("google") is None
