from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.config.key_manager import KeyManager


def _build_settings() -> AgentSettings:
    return AgentSettings(
        default_model="gpt-4o-mini",
        anthropic_api_key=None,
        openai_api_key=None,
        azure_openai_api_key=None,
        google_api_key=None,
        huggingface_api_key=None,
        openrouter_api_key=None,
    )


def test_set_key_writes_env_file_and_hot_reloads_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_path = tmp_path / ".agent_cli" / ".env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = _build_settings()
    manager = KeyManager(settings, dotenv_path=dotenv_path)

    assert manager.set_key("openai", "OPENAI_API_KEY", "  sk-test-openai  ") is True
    assert dotenv_path.exists() is True
    assert "OPENAI_API_KEY=sk-test-openai" in dotenv_path.read_text(encoding="utf-8")
    assert os.environ["OPENAI_API_KEY"] == "sk-test-openai"
    assert settings.openai_api_key == "sk-test-openai"
    assert manager.is_key_set("openai") is True
    assert manager.get_key_source("OPENAI_API_KEY") == "dotenv"


def test_delete_key_removes_env_file_and_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_path = tmp_path / ".agent_cli" / ".env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = _build_settings()
    manager = KeyManager(settings, dotenv_path=dotenv_path)
    assert manager.set_key("openai", "OPENAI_API_KEY", "sk-delete-me") is True

    assert manager.delete_key("openai", "OPENAI_API_KEY") is True
    assert "OPENAI_API_KEY" not in os.environ
    assert settings.openai_api_key is None
    assert manager.is_key_set("openai") is False
    assert manager.get_key_source("OPENAI_API_KEY") == "none"
    if dotenv_path.exists():
        assert "OPENAI_API_KEY" not in dotenv_path.read_text(encoding="utf-8")


def test_set_key_creates_missing_dotenv_parent_directory(tmp_path: Path) -> None:
    dotenv_path = tmp_path / "nested" / "config" / ".env"
    manager = KeyManager(_build_settings(), dotenv_path=dotenv_path)

    assert manager.set_key("google", "GOOGLE_API_KEY", "g-test") is True
    assert dotenv_path.exists() is True
    assert dotenv_path.parent.is_dir() is True


def test_get_key_source_reports_env_when_value_only_exists_in_process_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "env-google")
    manager = KeyManager(_build_settings(), dotenv_path=tmp_path / ".env")

    assert manager.get_key_source("GOOGLE_API_KEY") == "env"


def test_get_key_source_prefers_dotenv_over_process_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "env-google")
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("GOOGLE_API_KEY=dotenv-google\n", encoding="utf-8")
    manager = KeyManager(_build_settings(), dotenv_path=dotenv_path)

    assert manager.get_key_source("GOOGLE_API_KEY") == "dotenv"


def test_find_settings_field_uses_alias_metadata(tmp_path: Path) -> None:
    manager = KeyManager(_build_settings(), dotenv_path=tmp_path / ".env")

    assert manager._find_settings_field("OPENAI_API_KEY") == "openai_api_key"
    assert manager._find_settings_field("HF_TOKEN") == "huggingface_api_key"
    assert manager._find_settings_field("UNKNOWN_ENV_VAR") is None


def test_set_key_rejects_empty_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    manager = KeyManager(_build_settings(), dotenv_path=dotenv_path)

    assert manager.set_key("openai", "OPENAI_API_KEY", "   ") is False
    assert dotenv_path.exists() is False
    assert "OPENAI_API_KEY" not in os.environ


def test_unmapped_env_var_still_persists_without_settings_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_PROVIDER_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    settings = _build_settings()
    manager = KeyManager(settings, dotenv_path=dotenv_path)

    assert manager.set_key("custom", "CUSTOM_PROVIDER_API_KEY", "custom-secret") is True
    assert os.environ["CUSTOM_PROVIDER_API_KEY"] == "custom-secret"
    assert manager.get_key_source("CUSTOM_PROVIDER_API_KEY") == "dotenv"
    assert manager.is_key_set("custom") is False
    assert getattr(settings, "custom_provider_api_key", None) is None


def test_delete_key_succeeds_even_when_dotenv_file_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    settings = _build_settings()
    settings.openrouter_api_key = "or-key"
    manager = KeyManager(settings, dotenv_path=tmp_path / ".env")

    assert manager.delete_key("openrouter", "OPENROUTER_API_KEY") is True
    assert "OPENROUTER_API_KEY" not in os.environ
    assert settings.openrouter_api_key is None
