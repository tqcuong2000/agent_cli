"""Read-only registry for data-driven defaults."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any

import tomllib

from agent_cli.core.models.config_models import (
    CapabilityObservation,
    CapabilitySnapshot,
    CapabilitySpec,
    EffortCapabilitySpec,
    ModelSpec,
    NativeToolsCapabilitySpec,
    ProviderConfig,
    ProviderSpec,
    WebSearchCapabilitySpec,
    effort_values,
)


class DataRegistry:
    """Read-only registry of system defaults loaded from package data files."""

    __slots__ = (
        "_data_root",
        "_models",
        "_providers",
        "_tools",
        "_memory",
        "_schema",
        "_prompt_cache",
        "_provider_specs_cache",
        "_model_specs_cache",
        "_model_lookup_cache",
        "_capability_observations",
        "_capability_cache_version",
    )

    def __init__(self) -> None:
        self._data_root = resources.files("agent_cli.data")
        self._models = self._load_toml("models.toml")
        self._providers = self._load_toml("providers.toml")
        self._tools = self._load_toml("tools.toml")
        self._memory = self._load_toml("memory.toml")
        self._schema = self._load_toml("schema.toml")
        self._prompt_cache: dict[str, str] = {}
        self._provider_specs_cache: dict[str, ProviderSpec] | None = None
        self._model_specs_cache: dict[str, ModelSpec] | None = None
        self._model_lookup_cache: dict[str, str] | None = None
        self._capability_observations: dict[
            tuple[str, str, str],
            dict[str, CapabilityObservation],
        ] = {}
        self._capability_cache_version = 1

    # -- Model Data -------------------------------------------------

    def get_context_window(self, model: str) -> int:
        resolved_spec = self.resolve_model_spec(model)
        if resolved_spec is not None:
            return int(resolved_spec.context_window)
        return 128_000

    def get_pricing(self, model: str) -> dict[str, float]:
        resolved_spec = self.resolve_model_spec(model)
        if resolved_spec is not None:
            return {
                "input": float(resolved_spec.pricing_input),
                "output": float(resolved_spec.pricing_output),
            }
        return {"input": 0.0, "output": 0.0}

    def get_builtin_providers(self) -> dict[str, ProviderConfig]:
        providers: dict[str, ProviderConfig] = {}
        for name, spec in self._get_provider_specs_cached().items():
            providers[name] = ProviderConfig(
                adapter_type=spec.adapter_type,
                base_url=spec.base_url,
                models=list(spec.models),
                api_key_env=spec.api_key_env,
                default_model=spec.default_model,
                supports_native_tools=True,
                max_context_tokens=spec.max_context_tokens,
            )

        return providers

    def get_tokenizer_encoding(self, model: str) -> str:
        resolved_spec = self.resolve_model_spec(model)
        if resolved_spec is not None and resolved_spec.tokenizer.strip():
            return str(resolved_spec.tokenizer)
        return "cl100k_base"

    def get_internal_models(self) -> dict[str, str]:
        internal = self._models.get("internal_models", {})
        return {
            "routing_model": str(internal.get("routing_model", "")),
            "summarization_model": str(internal.get("summarization_model", "")),
        }

    # -- Tools ------------------------------------------------------

    def get_safe_command_patterns(self) -> list[str]:
        shell = self._mapping(self._tools.get("shell"))
        patterns = shell.get("safe_command_patterns", [])
        if not isinstance(patterns, list):
            return []
        return [str(pattern) for pattern in patterns]

    def get_tool_defaults(self) -> dict[str, Any]:
        return deepcopy(self._tools)

    def get_web_search_defaults(self) -> dict[str, Any]:
        """Return global data-driven defaults for provider-managed web search."""
        web_search = self._mapping(self._tools.get("web_search"))
        defaults = self._mapping(web_search.get("defaults"))
        return deepcopy(defaults)

    def get_web_search_provider_defaults(self, provider_name: str) -> dict[str, Any]:
        """Return merged global + provider-specific web-search defaults."""
        provider_defaults = {}
        provider_specs = self._get_provider_specs_cached()
        provider_spec = provider_specs.get(str(provider_name).strip())
        if provider_spec is not None:
            provider_defaults = deepcopy(provider_spec.web_search)

        merged = self.get_web_search_defaults()
        merged.update(provider_defaults)
        return merged

    def get_provider_specs(self) -> dict[str, ProviderSpec]:
        """Return typed provider specifications."""
        return deepcopy(self._get_provider_specs_cached())

    def get_model_specs(self) -> dict[str, ModelSpec]:
        """Return typed model specifications."""
        return deepcopy(self._get_model_specs_cached())

    def resolve_model_spec(self, model_name: str) -> ModelSpec | None:
        """Resolve a model identifier against model ID/API model/aliases."""
        raw_name = str(model_name or "").strip()
        if not raw_name:
            return None

        specs = self._get_model_specs_cached()
        lookup = self._get_model_lookup_cached()
        resolved_id = lookup.get(raw_name.lower())
        if resolved_id is None:
            return None
        spec = specs.get(resolved_id)
        if spec is None:
            return None
        return deepcopy(spec)

    def get_model_capabilities(self, model_name: str) -> CapabilitySpec | None:
        """Return typed capabilities for a resolved model, if present."""
        spec = self.resolve_model_spec(model_name)
        if spec is None:
            return None
        return deepcopy(spec.capabilities)

    def get_capability_snapshot(
        self,
        provider: str,
        model: str,
        deployment_id: str,
        *,
        max_age_seconds: int = 900,
    ) -> CapabilitySnapshot:
        """Return declared/observed/effective capability state for an identity."""
        key = self._capability_key(provider, model, deployment_id)
        now = datetime.now(timezone.utc)
        observed_raw = self._capability_observations.get(key, {})
        observed = {
            name: self._normalize_observation(obs, default_source="cache")
            for name, obs in observed_raw.items()
        }

        spec = self.resolve_model_spec(model)
        declared = (
            spec.capabilities if spec is not None else self._default_capability_spec()
        )

        effective: dict[str, CapabilityObservation] = {}
        for capability_name in self._capability_names():
            cached = observed.get(capability_name)
            if cached is not None and self._is_fresh_observation(
                cached,
                now=now,
                max_age_seconds=max_age_seconds,
            ):
                effective[capability_name] = cached
                continue

            if spec is None:
                effective[capability_name] = CapabilityObservation(
                    status="unknown",
                    reason="model_not_registered",
                    checked_at=now,
                    source="declared",
                )
                continue

            supported = self._declared_support(declared, capability_name)
            effective[capability_name] = CapabilityObservation(
                status="supported" if supported else "unsupported",
                reason="declared_supported" if supported else "declared_unsupported",
                checked_at=now,
                source="declared",
            )

        return CapabilitySnapshot(
            provider=str(provider).strip(),
            model=str(model).strip(),
            deployment_id=str(deployment_id).strip(),
            declared=deepcopy(declared),
            observed=deepcopy(observed),
            effective=effective,
        )

    def save_capability_observation(
        self,
        provider: str,
        model: str,
        deployment_id: str,
        observation: Mapping[str, CapabilityObservation | Mapping[str, Any]],
    ) -> None:
        """Save observed capability states for provider/model/deployment identity."""
        key = self._capability_key(provider, model, deployment_id)
        existing = dict(self._capability_observations.get(key, {}))

        for capability_name, raw in observation.items():
            normalized_name = str(capability_name).strip()
            if not normalized_name:
                continue
            existing[normalized_name] = self._normalize_observation(raw)

        self._capability_observations[key] = existing

    def invalidate_capability_observations(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        deployment_id: str | None = None,
    ) -> int:
        """Invalidate capability observations by identity filter."""
        provider_norm = str(provider).strip().lower() if provider is not None else None
        model_norm = str(model).strip().lower() if model is not None else None
        deployment_norm = (
            str(deployment_id).strip().lower() if deployment_id is not None else None
        )

        removed = 0
        for key in list(self._capability_observations.keys()):
            p, m, d = key
            if provider_norm is not None and p != provider_norm:
                continue
            if model_norm is not None and m != model_norm:
                continue
            if deployment_norm is not None and d != deployment_norm:
                continue
            del self._capability_observations[key]
            removed += 1
        return removed

    def bump_capability_cache_version(self) -> int:
        """Invalidate all cached capability observations by version bump."""
        self._capability_cache_version += 1
        self._capability_observations.clear()
        return self._capability_cache_version

    @property
    def capability_cache_version(self) -> int:
        """Current cache version for observed capability state."""
        return int(self._capability_cache_version)

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

    def _get_provider_specs_cached(self) -> dict[str, ProviderSpec]:
        cached = self._provider_specs_cache
        if cached is not None:
            return cached

        providers_data = self._mapping(self._providers.get("providers"))

        parsed: dict[str, ProviderSpec] = {}
        for name, raw in providers_data.items():
            data = self._mapping(raw)
            if not data:
                continue
            provider_name = str(name).strip()
            if not provider_name:
                continue

            parsed[provider_name] = ProviderSpec(
                name=provider_name,
                adapter_type=str(data.get("adapter_type", "openai_compatible")),
                base_url=(
                    str(data.get("base_url")).strip() if data.get("base_url") else None
                ),
                models=[str(model) for model in data.get("models", [])],
                api_key_env=(
                    str(data.get("api_key_env")).strip()
                    if data.get("api_key_env")
                    else None
                ),
                default_model=(
                    str(data.get("default_model")).strip()
                    if data.get("default_model")
                    else None
                ),
                max_context_tokens=self._to_optional_int(
                    data.get("max_context_tokens")
                ),
                web_search=self._mapping(data.get("web_search")),
            )

        self._provider_specs_cache = parsed
        return parsed

    def _get_model_specs_cached(self) -> dict[str, ModelSpec]:
        cached = self._model_specs_cache
        if cached is not None:
            return cached

        models_data = self._mapping(self._models.get("models"))
        parsed: dict[str, ModelSpec] = {}
        lookup: dict[str, str] = {}

        allowed_efforts = set(effort_values())
        allowed_web_modes = {"provider_native", "responses_api", "none"}

        for key, raw in models_data.items():
            model_id = str(key).strip()
            if not model_id:
                continue
            data = self._mapping(raw)
            if not data:
                continue

            capabilities_raw = self._mapping(data.get("capabilities"))
            if not capabilities_raw:
                raise RuntimeError(
                    f"Missing required capabilities block for model: {model_id}"
                )

            native_raw = self._mapping(capabilities_raw.get("native_tools"))
            effort_raw = self._mapping(capabilities_raw.get("effort"))
            web_raw = self._mapping(capabilities_raw.get("web_search"))

            if not native_raw:
                raise RuntimeError(
                    f"Missing required capabilities.native_tools for model: {model_id}"
                )
            if not effort_raw:
                raise RuntimeError(
                    f"Missing required capabilities.effort for model: {model_id}"
                )
            if not web_raw:
                raise RuntimeError(
                    f"Missing required capabilities.web_search for model: {model_id}"
                )

            native_supported = self._to_required_bool(
                native_raw.get("supported"),
                f"models.{model_id}.capabilities.native_tools.supported",
            )
            effort_supported = self._to_required_bool(
                effort_raw.get("supported"),
                f"models.{model_id}.capabilities.effort.supported",
            )
            web_supported = self._to_required_bool(
                web_raw.get("supported"),
                f"models.{model_id}.capabilities.web_search.supported",
            )

            levels_raw = effort_raw.get("levels")
            if not isinstance(levels_raw, list) or not levels_raw:
                raise RuntimeError(
                    "Invalid typed payload in models."
                    f"{model_id}.capabilities.effort.levels: "
                    "must be a non-empty list of effort levels."
                )
            levels = [str(level).strip().lower() for level in levels_raw if str(level)]
            if not levels:
                raise RuntimeError(
                    "Invalid typed payload in models."
                    f"{model_id}.capabilities.effort.levels: "
                    "must include at least one non-empty value."
                )
            for level in levels:
                if level not in allowed_efforts:
                    allowed = ", ".join(sorted(allowed_efforts))
                    raise RuntimeError(
                        "Invalid typed payload in models."
                        f"{model_id}.capabilities.effort.levels: "
                        f"unsupported level '{level}'. Allowed: {allowed}"
                    )

            web_mode = str(web_raw.get("mode", "none")).strip().lower()
            if web_mode not in allowed_web_modes:
                allowed = ", ".join(sorted(allowed_web_modes))
                raise RuntimeError(
                    "Invalid typed payload in models."
                    f"{model_id}.capabilities.web_search.mode: "
                    f"unsupported mode '{web_mode}'. Allowed: {allowed}"
                )

            aliases_raw = data.get("aliases", [])
            aliases: list[str] = []
            if isinstance(aliases_raw, list):
                aliases = [str(alias).strip() for alias in aliases_raw if str(alias)]

            spec = ModelSpec(
                model_id=model_id,
                provider=str(data.get("provider", "")).strip(),
                api_model=str(data.get("api_model", "")).strip() or model_id,
                aliases=aliases,
                context_window=max(
                    self._to_int(data.get("context_window"), 128_000), 1
                ),
                tokenizer=str(data.get("tokenizer", "cl100k_base")).strip()
                or "cl100k_base",
                pricing_input=float(data.get("pricing_input", 0.0)),
                pricing_output=float(data.get("pricing_output", 0.0)),
                capabilities=CapabilitySpec(
                    native_tools=NativeToolsCapabilitySpec(supported=native_supported),
                    effort=EffortCapabilitySpec(
                        supported=effort_supported,
                        levels=levels,
                    ),
                    web_search=WebSearchCapabilitySpec(
                        supported=web_supported,
                        mode=web_mode,
                        tool_type=str(web_raw.get("tool_type", "")).strip(),
                    ),
                ),
            )

            parsed[model_id] = spec

            for alias in [model_id, spec.api_model, *spec.aliases]:
                normalized = str(alias).strip().lower()
                if not normalized:
                    continue
                existing = lookup.get(normalized)
                if existing is not None and existing != model_id:
                    raise RuntimeError(
                        "Duplicate model alias detected in models.toml: "
                        f"'{alias}' is used by both '{existing}' and '{model_id}'."
                    )
                lookup[normalized] = model_id

        self._model_specs_cache = parsed
        self._model_lookup_cache = lookup
        return parsed

    def _get_model_lookup_cached(self) -> dict[str, str]:
        lookup = self._model_lookup_cache
        if lookup is not None:
            return lookup
        self._get_model_specs_cached()
        return self._model_lookup_cache or {}

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _to_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_required_bool(value: Any, path: str) -> bool:
        if isinstance(value, bool):
            return value
        raise RuntimeError(f"Invalid typed payload in {path}: must be boolean.")

    @staticmethod
    def _capability_names() -> tuple[str, str, str]:
        return ("native_tools", "effort", "web_search")

    @staticmethod
    def _default_capability_spec() -> CapabilitySpec:
        return CapabilitySpec(
            native_tools=NativeToolsCapabilitySpec(supported=False),
            effort=EffortCapabilitySpec(supported=False, levels=["auto"]),
            web_search=WebSearchCapabilitySpec(supported=False, mode="none"),
        )

    @staticmethod
    def _declared_support(spec: CapabilitySpec, capability_name: str) -> bool:
        if capability_name == "native_tools":
            return bool(spec.native_tools.supported)
        if capability_name == "effort":
            return bool(spec.effort.supported)
        if capability_name == "web_search":
            return bool(spec.web_search.supported)
        return False

    @staticmethod
    def _capability_key(
        provider: str, model: str, deployment_id: str
    ) -> tuple[str, str, str]:
        return (
            str(provider).strip().lower(),
            str(model).strip().lower(),
            str(deployment_id).strip().lower(),
        )

    @staticmethod
    def _normalize_observation(
        observation: CapabilityObservation | Mapping[str, Any],
        *,
        default_source: str = "probe",
    ) -> CapabilityObservation:
        if isinstance(observation, CapabilityObservation):
            status = str(observation.status).strip().lower()
            if status not in {"supported", "unsupported", "unknown"}:
                status = "unknown"
            return CapabilityObservation(
                status=status,
                reason=str(observation.reason or "").strip(),
                checked_at=observation.checked_at,
                source=str(observation.source or default_source).strip()
                or default_source,
            )

        mapping = DataRegistry._mapping(observation)
        status = str(mapping.get("status", "unknown")).strip().lower()
        if status not in {"supported", "unsupported", "unknown"}:
            status = "unknown"

        checked_at = mapping.get("checked_at")
        parsed_checked_at: datetime | None = None
        if isinstance(checked_at, datetime):
            parsed_checked_at = checked_at
        elif isinstance(checked_at, str) and checked_at.strip():
            raw = checked_at.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                parsed_checked_at = datetime.fromisoformat(raw)
            except ValueError:
                parsed_checked_at = None

        return CapabilityObservation(
            status=status,
            reason=str(mapping.get("reason", "")).strip(),
            checked_at=parsed_checked_at,
            source=str(mapping.get("source", default_source)).strip() or default_source,
        )

    @staticmethod
    def _is_fresh_observation(
        observation: CapabilityObservation,
        *,
        now: datetime,
        max_age_seconds: int,
    ) -> bool:
        checked = observation.checked_at
        if checked is None:
            return False
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if max_age_seconds < 0:
            return True
        return checked >= now - timedelta(seconds=int(max_age_seconds))

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        """Normalize unknown values to a plain dictionary."""
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
        return {}
