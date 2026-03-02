"""Read-only registry for data-driven defaults."""

from __future__ import annotations

from copy import deepcopy
from importlib import resources
from typing import Any

import tomllib

from agent_cli.core.models.config_models import EffortLevel, ProviderConfig


class DataRegistry:
    """Read-only registry of system defaults loaded from package data files."""

    __slots__ = (
        "_data_root",
        "_models",
        "_effort",
        "_tools",
        "_memory",
        "_schema",
        "_prompt_cache",
    )

    def __init__(self) -> None:
        self._data_root = resources.files("agent_cli.data")
        self._models = self._load_toml("models.toml")
        self._effort = self._load_toml("effort.toml")
        self._tools = self._load_toml("tools.toml")
        self._memory = self._load_toml("memory.toml")
        self._schema = self._load_toml("schema.toml")
        self._prompt_cache: dict[str, str] = {}

    # -- Model Data -------------------------------------------------

    def get_context_window(self, model: str) -> int:
        model_lower = model.lower()
        context_windows = self._models.get("context_windows", {})

        for name, tokens in context_windows.items():
            if str(name).lower() == model_lower:
                return int(tokens)

        best_tokens: int | None = None
        best_prefix_len = -1
        for item in self._models.get("context_window_prefixes", []):
            if not isinstance(item, dict):
                continue
            prefix = str(item.get("prefix", "")).lower()
            if not prefix or not model_lower.startswith(prefix):
                continue
            if len(prefix) > best_prefix_len:
                best_prefix_len = len(prefix)
                best_tokens = int(item.get("tokens", 0))

        if best_tokens is not None:
            return best_tokens

        default_context = context_windows.get("default_context_window")
        if default_context is None:
            default_context = self._models.get("default_context_window")
        if default_context is None:
            for item in self._models.get("context_window_prefixes", []):
                if isinstance(item, dict) and "default_context_window" in item:
                    default_context = item["default_context_window"]
        return int(default_context or 128_000)

    def get_pricing(self, model: str) -> dict[str, float]:
        model_lower = model.lower()
        pricing_data = self._models.get("pricing", {})

        selected: dict[str, Any] | None = None
        for name, data in pricing_data.items():
            if str(name).lower() == model_lower and isinstance(data, dict):
                selected = data
                break

        if selected is None:
            selected = pricing_data.get(
                "default_pricing", {"input": 0.0, "output": 0.0}
            )

        return {
            "input": float(selected.get("input", 0.0)),
            "output": float(selected.get("output", 0.0)),
        }

    def get_builtin_providers(self) -> dict[str, ProviderConfig]:
        providers_data = self._models.get("providers", {})
        providers: dict[str, ProviderConfig] = {}

        for name, data in providers_data.items():
            if not isinstance(data, dict):
                continue
            providers[name] = ProviderConfig(
                adapter_type=str(data.get("adapter_type", "openai_compatible")),
                base_url=data.get("base_url"),
                models=[str(model) for model in data.get("models", [])],
                api_key_env=data.get("api_key_env"),
                default_model=data.get("default_model"),
                supports_native_tools=bool(data.get("supports_native_tools", True)),
                max_context_tokens=data.get("max_context_tokens"),
            )

        return providers

    def get_tokenizer_encoding(self, model: str) -> str:
        tokenizer = self._models.get("tokenizer", {})
        model_lower = model.lower()
        prefixes = [
            str(prefix).lower() for prefix in tokenizer.get("o200k_prefixes", [])
        ]
        if any(model_lower.startswith(prefix) for prefix in prefixes):
            return "o200k_base"
        return str(tokenizer.get("default_encoding", "cl100k_base"))

    def get_internal_models(self) -> dict[str, str]:
        internal = self._models.get("internal_models", {})
        return {
            "routing_model": str(internal.get("routing_model", "")),
            "summarization_model": str(internal.get("summarization_model", "")),
        }

    # -- Effort -----------------------------------------------------

    def get_effort_constraints(self, level: EffortLevel) -> dict[str, Any]:
        key = level.name if isinstance(level, EffortLevel) else str(level).upper()
        constraints = self._effort.get(key)
        if not isinstance(constraints, dict):
            raise KeyError(f"Unknown effort level: {key}")
        return deepcopy(constraints)

    # -- Tools ------------------------------------------------------

    def get_safe_command_patterns(self) -> list[str]:
        shell = self._tools.get("shell", {})
        return [str(pattern) for pattern in shell.get("safe_command_patterns", [])]

    def get_tool_defaults(self) -> dict[str, Any]:
        return deepcopy(self._tools)

    # -- Memory -----------------------------------------------------

    def get_context_budget(self) -> dict[str, float]:
        return deepcopy(self._memory.get("context_budget", {}))

    def get_retry_defaults(self) -> dict[str, Any]:
        return deepcopy(self._memory.get("retry", {}))

    def get_session_defaults(self) -> dict[str, Any]:
        return deepcopy(self._memory.get("session", {}))

    def get_summarizer_defaults(self) -> dict[str, Any]:
        return deepcopy(self._memory.get("summarizer", {}))

    def get_token_counter_defaults(self) -> dict[str, Any]:
        return deepcopy(self._memory.get("token_counter", {}))

    def get_stuck_detector_defaults(self) -> dict[str, Any]:
        return deepcopy(self._memory.get("stuck_detector", {}))

    # -- Schema -----------------------------------------------------

    def get_schema_defaults(self) -> dict[str, Any]:
        return deepcopy(self._schema)

    # -- Prompts ----------------------------------------------------

    def get_prompt_template(self, name: str) -> str:
        cached = self._prompt_cache.get(name)
        if cached is not None:
            return cached

        prompt_path = self._data_root.joinpath("prompts", f"{name}.txt")
        try:
            template = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Prompt template not found: {name}") from exc

        self._prompt_cache[name] = template
        return template

    # -- Internal ---------------------------------------------------

    def _load_toml(self, filename: str) -> dict[str, Any]:
        file_path = self._data_root.joinpath(filename)
        try:
            with file_path.open("rb") as handle:
                loaded = tomllib.load(handle)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Missing data file: {filename}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(f"Malformed TOML in data file: {filename}") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to load data file: {filename}") from exc

        if not isinstance(loaded, dict):
            raise RuntimeError(f"Data file did not parse to a mapping: {filename}")
        return loaded
