"""Service for managing provider API keys across file and runtime state."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, set_key, unset_key

from agent_cli.core.infra.config.config import AgentSettings

logger = logging.getLogger(__name__)

KeySource = Literal["env", "dotenv", "none"]


class KeyManager:
    """Persist and hot-reload provider API keys."""

    def __init__(
        self,
        settings: AgentSettings,
        dotenv_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._dotenv_path = dotenv_path or Path.home() / ".agent_cli" / ".env"

    def set_key(self, provider_name: str, env_var: str, value: str) -> bool:
        """Write a key to `.env`, `os.environ`, and the live settings object."""
        normalized_env_var = str(env_var).strip()
        normalized_value = str(value).strip()
        if not normalized_env_var or not normalized_value:
            return False

        try:
            self._ensure_dotenv_file()
            set_key(
                str(self._dotenv_path),
                normalized_env_var,
                normalized_value,
                quote_mode="never",
            )
            os.environ[normalized_env_var] = normalized_value

            settings_field = self._find_settings_field(normalized_env_var)
            if settings_field is not None:
                setattr(self._settings, settings_field, normalized_value)

            logger.info(
                "API key stored for provider '%s' in %s",
                str(provider_name).strip() or "unknown",
                self._dotenv_path,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to store API key for provider '%s'",
                str(provider_name).strip() or "unknown",
            )
            return False

    def delete_key(self, provider_name: str, env_var: str) -> bool:
        """Delete a key from `.env`, `os.environ`, and the live settings object."""
        normalized_env_var = str(env_var).strip()
        if not normalized_env_var:
            return False

        try:
            if self._dotenv_path.exists():
                unset_key(
                    str(self._dotenv_path),
                    normalized_env_var,
                    quote_mode="never",
                )
            os.environ.pop(normalized_env_var, None)

            settings_field = self._find_settings_field(normalized_env_var)
            if settings_field is not None:
                setattr(self._settings, settings_field, None)

            logger.info(
                "API key removed for provider '%s' from %s",
                str(provider_name).strip() or "unknown",
                self._dotenv_path,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to delete API key for provider '%s'",
                str(provider_name).strip() or "unknown",
            )
            return False

    def is_key_set(self, provider_name: str) -> bool:
        """Check if a key is currently available for the provider."""
        key = self._settings.resolve_api_key(provider_name)
        return bool(key and str(key).strip())

    def get_key_source(self, env_var: str) -> KeySource:
        """Detect where the current key value originates from."""
        normalized_env_var = str(env_var).strip()
        if not normalized_env_var:
            return "none"

        dotenv_data = self._load_dotenv_values()
        if self._has_value(dotenv_data.get(normalized_env_var)):
            return "dotenv"
        if self._has_value(os.environ.get(normalized_env_var)):
            return "env"
        return "none"

    def _find_settings_field(self, env_var: str) -> str | None:
        """Reverse-resolve the AgentSettings field name from an env alias."""
        normalized_env_var = str(env_var).strip()
        if not normalized_env_var:
            return None

        for field_name, field_info in AgentSettings.model_fields.items():
            if str(getattr(field_info, "alias", "")).strip() == normalized_env_var:
                return field_name
        return None

    def _ensure_dotenv_file(self) -> None:
        """Create the target `.env` path if it does not already exist."""
        self._dotenv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._dotenv_path.exists():
            self._dotenv_path.touch()

    def _load_dotenv_values(self) -> dict[str, str | None]:
        """Load current `.env` values, returning an empty mapping if missing."""
        if not self._dotenv_path.exists():
            return {}

        loaded = dotenv_values(str(self._dotenv_path))
        return {str(key): value for key, value in loaded.items()}

    @staticmethod
    def _has_value(value: object) -> bool:
        return bool(value is not None and str(value).strip())
