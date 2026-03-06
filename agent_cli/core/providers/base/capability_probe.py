"""Runtime capability probing for provider/model/deployment identities."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_cli.core.infra.config.config_models import (
    CapabilityObservation,
    CapabilitySnapshot,
)
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider

if TYPE_CHECKING:
    from agent_cli.core.infra.logging.logging import ObservabilityManager

logger = logging.getLogger(__name__)


class CapabilityProbeService:
    """Collect and persist observed provider capability states."""

    def __init__(
        self,
        data_registry: DataRegistry,
        *,
        observability: ObservabilityManager | None = None,
        max_age_seconds: int = 900,
    ) -> None:
        self._data_registry = data_registry
        self._observability = observability
        self._max_age_seconds = max(int(max_age_seconds), 0)

    def probe_provider(
        self,
        provider: BaseLLMProvider,
        *,
        trigger: str = "runtime",
    ) -> CapabilitySnapshot:
        """Probe and persist capability observations for one provider instance."""
        provider_name = str(getattr(provider, "provider_name", "")).strip() or "unknown"
        model_name = str(getattr(provider, "model_name", "")).strip() or "unknown"
        base_url = str(getattr(provider, "base_url", "") or "").strip()
        deployment_id = self._build_deployment_id(
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
        )

        try:
            observations = self._build_observations(
                provider=provider,
                provider_name=provider_name,
                trigger=trigger,
            )
            self._data_registry.save_capability_observation(
                provider=provider_name,
                model=model_name,
                deployment_id=deployment_id,
                observation=observations,
            )

            snapshot = self._data_registry.get_capability_snapshot(
                provider=provider_name,
                model=model_name,
                deployment_id=deployment_id,
                max_age_seconds=self._max_age_seconds,
            )
            self._record_unknown_fallbacks(snapshot)
            self._record_counter("probe_successes")
            logger.info(
                "Capability probe completed",
                extra={
                    "source": "capability_probe",
                    "data": {
                        "provider": provider_name,
                        "model": model_name,
                        "deployment_id": deployment_id,
                        "trigger": str(trigger),
                    },
                },
            )
            return snapshot
        except Exception:
            self._record_counter("probe_failures")
            logger.exception(
                "Capability probe failed for %s/%s (%s)",
                provider_name,
                model_name,
                trigger,
            )
            return self._data_registry.get_capability_snapshot(
                provider=provider_name,
                model=model_name,
                deployment_id=deployment_id,
                max_age_seconds=self._max_age_seconds,
            )

    def _build_observations(
        self,
        *,
        provider: BaseLLMProvider,
        provider_name: str,
        trigger: str,
    ) -> dict[str, CapabilityObservation]:
        now = datetime.now(timezone.utc)
        source = "probe"

        observations: dict[str, CapabilityObservation] = {
            "native_tools": CapabilityObservation(
                status="supported"
                if bool(provider.supports_native_tools)
                else "unsupported",
                reason=(
                    f"provider_runtime_native_tools_supported:{trigger}"
                    if bool(provider.supports_native_tools)
                    else f"provider_runtime_native_tools_unsupported:{trigger}"
                ),
                checked_at=now,
                source=source,
            ),
            "effort": CapabilityObservation(
                status="supported" if bool(provider.supports_effort) else "unsupported",
                reason=(
                    f"provider_runtime_effort_supported:{trigger}"
                    if bool(provider.supports_effort)
                    else f"provider_runtime_effort_unsupported:{trigger}"
                ),
                checked_at=now,
                source=source,
            ),
        }

        observations["web_search"] = self._probe_web_search(
            provider=provider,
            provider_name=provider_name,
            now=now,
            source=source,
            trigger=trigger,
        )
        return observations

    def _probe_web_search(
        self,
        *,
        provider: BaseLLMProvider,
        provider_name: str,
        now: datetime,
        source: str,
        trigger: str,
    ) -> CapabilityObservation:
        if provider_name == "azure":
            if self._provider_api_surface(provider) != "responses_api":
                return CapabilityObservation(
                    status="unsupported",
                    reason=f"azure_api_surface_not_responses_api:{trigger}",
                    checked_at=now,
                    source=source,
                )
            cached_support = getattr(
                provider, "_azure_responses_web_search_supported", None
            )
            if cached_support is False:
                return CapabilityObservation(
                    status="unsupported",
                    reason=f"azure_responses_api_previously_rejected:{trigger}",
                    checked_at=now,
                    source=source,
                )
            client = getattr(provider, "client", None)
            responses_api_available = bool(
                client is not None
                and hasattr(client, "responses")
                and hasattr(client.responses, "create")
            )
            if not responses_api_available:
                return CapabilityObservation(
                    status="unsupported",
                    reason=f"azure_responses_api_unavailable_in_sdk_client:{trigger}",
                    checked_at=now,
                    source=source,
                )
            return CapabilityObservation(
                status="supported",
                reason=f"azure_responses_api_available_runtime:{trigger}",
                checked_at=now,
                source=source,
            )

        if provider_name == "openai":
            # OpenAI web-search path is not integrated yet in this runtime.
            return CapabilityObservation(
                status="unsupported",
                reason=f"openai_web_search_not_integrated_runtime:{trigger}",
                checked_at=now,
                source=source,
            )

        return CapabilityObservation(
            status="supported" if bool(provider.supports_web_search) else "unsupported",
            reason=(
                f"provider_runtime_web_search_supported:{trigger}"
                if bool(provider.supports_web_search)
                else f"provider_runtime_web_search_unsupported:{trigger}"
            ),
            checked_at=now,
            source=source,
        )

    @staticmethod
    def _provider_api_surface(provider: BaseLLMProvider) -> str:
        raw = str(getattr(provider, "api_surface", "chat_completions")).strip().lower()
        if raw in {"responses_api", "responses"}:
            return "responses_api"
        return "chat_completions"

    def _record_unknown_fallbacks(self, snapshot: CapabilitySnapshot) -> None:
        unknown_count = sum(
            1
            for observed in snapshot.effective.values()
            if observed.status == "unknown"
        )
        if unknown_count > 0:
            self._record_counter("unknown_capability_fallbacks", count=unknown_count)

    def _record_counter(self, name: str, *, count: int = 1) -> None:
        if self._observability is None:
            return
        self._observability.record_migration_counter(name, count=count)

    @staticmethod
    def _build_deployment_id(
        *,
        provider_name: str,
        model_name: str,
        base_url: str = "",
    ) -> str:
        provider = str(provider_name).strip() or "unknown"
        model = str(model_name).strip() or "unknown"
        base = str(base_url).strip()
        if base:
            return f"{provider}:{model}@{base}"
        return f"{provider}:{model}"
