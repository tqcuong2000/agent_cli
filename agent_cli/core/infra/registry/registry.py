"""Read-only registry for data-driven defaults."""

from __future__ import annotations

import json
import logging
from threading import Lock
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any

from agent_cli.core.infra.config.config_models import (
    CapabilityObservation,
    CapabilitySnapshot,
    CapabilitySpec,
    EffortCapabilitySpec,
    EffortLevel,
    ModelSpec,
    NativeToolsCapabilitySpec,
    ProviderConfig,
    ProviderSpec,
    WebSearchCapabilitySpec,
    effort_values,
)

logger = logging.getLogger(__name__)


class DataRegistry:
    """Read-only registry of system defaults loaded from package data files."""

    _CAPABILITY_ACCESSORS = {
        "native_tools": lambda spec: spec.native_tools.supported,
        "effort": lambda spec: spec.effort.supported,
        "web_search": lambda spec: spec.web_search.supported,
    }

    __slots__ = (
        "_data_root",
        "_models",
        "_offerings",
        "_providers",
        "_tools",
        "_memory",
        "_schema",
        "_prompt_cache",
        "_provider_specs_cache",
        "_model_specs_cache",
        "_model_lookup_cache",
        "_capability_observations",
        "_capability_observations_lock",
        "_capability_cache_version",
    )

    def __init__(self) -> None:
        self._data_root = resources.files("agent_cli.data")
        self._models = self._load_json("models.json")
        self._offerings = self._load_offerings("models")
        self._providers = self._load_providers_dir("providers")
        self._tools = self._load_json("tools.json")
        self._memory = self._load_json("memory.json")
        self._schema = self._load_json("schema.json")
        self._prompt_cache: dict[str, str] = {}
        self._provider_specs_cache: dict[str, ProviderSpec] | None = None
        self._model_specs_cache: dict[str, ModelSpec] | None = None
        self._model_lookup_cache: dict[str, str] | None = None
        self._capability_observations: dict[
            tuple[str, str, str],
            dict[str, CapabilityObservation],
        ] = {}
        self._capability_observations_lock = Lock()
        self._capability_cache_version = 1
        logger.info(
            "DataRegistry loaded",
            extra={
                "source": "data_registry",
                "data": {
                    "offerings": len(self._offerings),
                    "providers": len(self._providers),
                },
            },
        )

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
                api_key_env=spec.api_key_env,
                supports_native_tools=True,
                max_context_tokens=spec.max_context_tokens,
                api_profile=deepcopy(spec.api_profile),
                require_verification=spec.require_verification,
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

    def get_provider_api_profile(self, provider_name: str) -> dict[str, Any]:
        """Return provider API profile contract payload."""
        provider_specs = self._get_provider_specs_cached()
        provider_spec = provider_specs.get(str(provider_name).strip())
        if provider_spec is None:
            return {}
        return deepcopy(provider_spec.api_profile)

    def get_provider_specs(self) -> dict[str, ProviderSpec]:
        """Return typed provider specifications."""
        return deepcopy(self._get_provider_specs_cached())

    def get_model_specs(self) -> dict[str, ModelSpec]:
        """Return typed model specifications."""
        return deepcopy(self._get_model_specs_cached())

    def resolve_model_spec(self, model_name: str) -> ModelSpec | None:
        """Resolve a model identifier against canonical and legacy lookup keys."""
        raw_name = str(model_name or "").strip()
        if not raw_name:
            logger.debug(
                "resolve_model_spec miss: empty model name",
                extra={
                    "source": "data_registry",
                    "data": {"input": str(model_name or "")},
                },
            )
            return None

        specs = self._get_model_specs_cached()
        lookup = self._get_model_lookup_cached()
        resolved_id = lookup.get(raw_name.lower())
        if resolved_id is None:
            logger.debug(
                "resolve_model_spec miss",
                extra={
                    "source": "data_registry",
                    "data": {"input": raw_name},
                },
            )
            return None
        spec = specs.get(resolved_id)
        if spec is None:
            logger.debug(
                "resolve_model_spec miss: dangling lookup",
                extra={
                    "source": "data_registry",
                    "data": {"input": raw_name, "resolved_id": resolved_id},
                },
            )
            return None
        logger.debug(
            "resolve_model_spec hit",
            extra={
                "source": "data_registry",
                "data": {
                    "input": raw_name,
                    "resolved_id": resolved_id,
                    "provider": spec.provider,
                },
            },
        )
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
        with self._capability_observations_lock:
            observed_raw = dict(self._capability_observations.get(key, {}))
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
        normalized_updates: dict[str, CapabilityObservation] = {}

        for capability_name, raw in observation.items():
            normalized_name = str(capability_name).strip()
            if not normalized_name:
                continue
            normalized_updates[normalized_name] = self._normalize_observation(raw)

        with self._capability_observations_lock:
            existing = dict(self._capability_observations.get(key, {}))
            existing.update(normalized_updates)
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

        with self._capability_observations_lock:
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
        with self._capability_observations_lock:
            self._capability_cache_version += 1
            self._capability_observations.clear()
            return self._capability_cache_version

    @property
    def capability_cache_version(self) -> int:
        """Current cache version for observed capability state."""
        with self._capability_observations_lock:
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

    def get_title_generation_defaults(self) -> dict[str, Any]:
        return deepcopy(self._schema.get("title", {}))

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

    def _load_json(self, filename: str) -> dict[str, Any]:
        file_path = self._data_root.joinpath(filename)
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Missing data file: {filename}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed JSON in data file: {filename}") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to load data file: {filename}") from exc

        if not isinstance(loaded, dict):
            raise RuntimeError(f"Data file did not parse to a mapping: {filename}")
        return loaded

    def _load_offerings(self, directory: str) -> dict[str, dict[str, Any]]:
        """Load offering definitions from `data/<directory>/*.json`."""
        dir_path = self._data_root.joinpath(directory)
        if not dir_path.is_dir():
            raise RuntimeError(f"Missing offerings directory: {directory}")

        offerings: dict[str, dict[str, Any]] = {}
        for file_path in sorted(dir_path.iterdir(), key=lambda path: path.name):
            if not file_path.is_file():
                continue
            file_name = str(file_path.name)
            if not file_name.lower().endswith(".json"):
                continue

            try:
                with file_path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Malformed JSON in offerings file: {directory}/{file_path.name}"
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to load offerings file: {directory}/{file_path.name}"
                ) from exc

            if not isinstance(loaded, dict):
                raise RuntimeError(
                    f"Offerings file did not parse to a mapping: {directory}/{file_path.name}"
                )

            grouped_offerings = self._mapping(loaded.get("offerings"))
            if "offerings" in loaded and not grouped_offerings:
                raise RuntimeError(
                    "Invalid offerings table in "
                    f"{directory}/{file_path.name}: expected non-empty mapping."
                )
            if grouped_offerings:
                for grouped_id, grouped_raw in grouped_offerings.items():
                    grouped_data = self._mapping(grouped_raw)
                    if not grouped_data:
                        raise RuntimeError(
                            "Invalid offering entry in "
                            f"{directory}/{file_path.name}: '{grouped_id}'."
                        )

                    model_id = (
                        str(grouped_data.get("id", "")).strip()
                        or str(grouped_id).strip()
                    )
                    self._register_offering(
                        offerings,
                        model_id=model_id,
                        data=grouped_data,
                        location=f"{directory}/{file_path.name}",
                    )
                continue

            model_id = str(loaded.get("id", "")).strip() or file_name[:-5]
            self._register_offering(
                offerings,
                model_id=model_id,
                data=loaded,
                location=f"{directory}/{file_path.name}",
            )

        if not offerings:
            raise RuntimeError(f"No offering files found in directory: {directory}")
        return offerings

    def _load_providers_dir(self, directory: str) -> dict[str, dict[str, Any]]:
        """Load provider definitions from `data/<directory>/*.json`."""
        dir_path = self._data_root.joinpath(directory)
        if not dir_path.is_dir():
            raise RuntimeError(f"Missing providers directory: {directory}")

        providers: dict[str, dict[str, Any]] = {}
        for file_path in sorted(dir_path.iterdir(), key=lambda path: path.name):
            if not file_path.is_file():
                continue
            file_name = str(file_path.name)
            if not file_name.lower().endswith(".json"):
                continue

            provider_name = file_name[:-5].strip()
            if not provider_name:
                raise RuntimeError(
                    f"Invalid provider file name in directory: {directory}/{file_name}"
                )
            if provider_name in providers:
                raise RuntimeError(
                    f"Duplicate provider id '{provider_name}' in {directory}/{file_name}."
                )

            try:
                with file_path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Malformed JSON in providers file: {directory}/{file_name}"
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to load providers file: {directory}/{file_name}"
                ) from exc

            if not isinstance(loaded, dict):
                raise RuntimeError(
                    f"Providers file did not parse to a mapping: {directory}/{file_name}"
                )

            providers[provider_name] = loaded

        if not providers:
            raise RuntimeError(f"No provider files found in directory: {directory}")
        return providers

    @staticmethod
    def _register_offering(
        offerings: dict[str, dict[str, Any]],
        *,
        model_id: str,
        data: Mapping[str, Any],
        location: str,
    ) -> None:
        """Register one offering with duplicate-id protection."""
        normalized_id = str(model_id).strip()
        if not normalized_id:
            raise RuntimeError(f"Missing offering id in {location}.")
        if normalized_id in offerings:
            raise RuntimeError(
                f"Duplicate offering id '{normalized_id}' in {location}."
            )
        offerings[normalized_id] = dict(data)

    def _get_provider_specs_cached(self) -> dict[str, ProviderSpec]:
        cached = self._provider_specs_cache
        if cached is not None:
            return cached

        parsed: dict[str, ProviderSpec] = {}
        for name, raw in self._providers.items():
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
                api_key_env=(
                    str(data.get("api_key_env")).strip()
                    if data.get("api_key_env")
                    else None
                ),
                max_context_tokens=self._to_optional_int(
                    data.get("max_context_tokens")
                ),
                api_profile=self._mapping(data.get("api_profile")),
                require_verification=self._to_bool(
                    data.get("require_verification"),
                    default=True,
                ),
            )

        self._provider_specs_cache = parsed
        return parsed

    def _get_model_specs_cached(self) -> dict[str, ModelSpec]:
        cached = self._model_specs_cache
        if cached is not None:
            return cached

        models_data = {name: dict(data) for name, data in self._offerings.items()}
        parsed: dict[str, ModelSpec] = {}
        lookup: dict[str, str] = {}

        allowed_efforts = set(effort_values())
        allowed_api_surfaces = {"", "chat_completions", "responses_api"}

        for key, raw in models_data.items():
            source_model_id = str(key).strip()
            if not source_model_id:
                continue
            data = self._mapping(raw)
            if not data:
                continue
            provider_name = str(data.get("provider", "")).strip().lower()
            canonical_model_id, model_ref = self._canonicalize_model_id(
                provider_name=provider_name,
                raw_model_id=source_model_id,
            )
            api_model_value = str(data.get("api_model", "")).strip() or model_ref
            # For legacy IDs like "ollama-gemma3-1b", prefer a colon-bearing
            # provider-scoped model_ref when api_model clearly encodes it.
            if ":" in api_model_value and model_ref == api_model_value.replace(":", "-"):
                model_ref = api_model_value
                canonical_model_id = f"{provider_name}:{model_ref}"
            if canonical_model_id in parsed:
                raise RuntimeError(
                    "Duplicate canonical model id detected in model offerings: "
                    f"'{canonical_model_id}'."
                )

            capabilities_raw = self._mapping(data.get("capabilities"))

            native_raw = self._mapping(capabilities_raw.get("native_tools"))
            effort_raw = self._mapping(capabilities_raw.get("effort"))
            web_raw = self._mapping(capabilities_raw.get("web_search"))

            native_supported = self._to_bool(native_raw.get("supported"), default=False)
            effort_supported = self._to_bool(effort_raw.get("supported"), default=False)
            web_supported = self._to_bool(web_raw.get("supported"), default=False)

            levels_raw = effort_raw.get("levels")
            if isinstance(levels_raw, list) and levels_raw:
                levels = [
                    str(level).strip().lower() for level in levels_raw if str(level)
                ]
            else:
                levels = [EffortLevel.AUTO.value]

            if not levels:
                levels = [EffortLevel.AUTO.value]

            for level in levels:
                if level not in allowed_efforts:
                    allowed = ", ".join(sorted(allowed_efforts))
                    raise RuntimeError(
                        "Invalid typed payload in offerings."
                        f"{source_model_id}.capabilities.effort.levels: "
                        f"unsupported level '{level}'. Allowed: {allowed}"
                    )

            api_surface = str(data.get("api_surface", "")).strip().lower()
            if api_surface not in allowed_api_surfaces:
                allowed = ", ".join(sorted(item for item in allowed_api_surfaces if item))
                raise RuntimeError(
                    "Invalid typed payload in offerings."
                    f"{source_model_id}.api_surface: "
                    f"unsupported value '{api_surface}'. Allowed: {allowed}"
                )

            plain_text = self._to_bool(data.get("plain_text"), default=False)

            spec = ModelSpec(
                model_id=canonical_model_id,
                provider=provider_name,
                model_ref=model_ref,
                api_model=api_model_value,
                api_surface=api_surface,
                plain_text=plain_text,
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
                        tool_type=str(web_raw.get("tool_type", "")).strip(),
                    ),
                ),
            )

            parsed[canonical_model_id] = spec

            for alias in [canonical_model_id, source_model_id]:
                normalized = str(alias).strip().lower()
                if not normalized:
                    continue
                existing = lookup.get(normalized)
                if existing is not None and existing != canonical_model_id:
                    raise RuntimeError(
                        "Duplicate offering lookup key detected in model offerings: "
                        f"'{alias}' is used by both '{existing}' and '{canonical_model_id}'."
                    )
                lookup[normalized] = canonical_model_id

        self._model_specs_cache = parsed
        self._model_lookup_cache = lookup
        return parsed

    @staticmethod
    def _canonicalize_model_id(
        *,
        provider_name: str,
        raw_model_id: str,
    ) -> tuple[str, str]:
        provider = str(provider_name or "").strip().lower()
        if not provider:
            raise RuntimeError(
                f"Model offering '{raw_model_id}' is missing a valid provider field."
            )

        raw = str(raw_model_id or "").strip()
        if not raw:
            raise RuntimeError("Model offering id cannot be empty.")

        provider_colon_prefix = f"{provider}:"
        if raw.lower().startswith(provider_colon_prefix):
            model_ref = raw[len(provider_colon_prefix) :].strip()
            if not model_ref:
                raise RuntimeError(
                    f"Model offering '{raw_model_id}' has invalid canonical id format."
                )
            return f"{provider}:{model_ref}", model_ref

        provider_dash_prefix = f"{provider}-"
        if raw.lower().startswith(provider_dash_prefix):
            model_ref = raw[len(provider_dash_prefix) :].strip()
            if model_ref:
                return f"{provider}:{model_ref}", model_ref

        return f"{provider}:{raw}", raw

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
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        return default

    @staticmethod
    def _capability_names() -> tuple[str, str, str]:
        return ("native_tools", "effort", "web_search")

    @staticmethod
    def _default_capability_spec() -> CapabilitySpec:
        return CapabilitySpec(
            native_tools=NativeToolsCapabilitySpec(supported=False),
            effort=EffortCapabilitySpec(supported=False, levels=["auto"]),
            web_search=WebSearchCapabilitySpec(supported=False),
        )

    @staticmethod
    def _declared_support(spec: CapabilitySpec, capability_name: str) -> bool:
        accessor = DataRegistry._CAPABILITY_ACCESSORS.get(capability_name)
        if accessor is None:
            return False
        return bool(accessor(spec))

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
