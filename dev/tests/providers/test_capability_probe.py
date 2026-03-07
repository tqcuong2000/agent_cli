"""Unit tests for runtime capability probing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.capability_probe import CapabilityProbeService
from agent_cli.core.providers.base.models import LLMResponse, ProviderRequestOptions, StreamChunk


@dataclass
class _CounterRecorder:
    calls: list[tuple[str, int]] = field(default_factory=list)

    def record_migration_counter(self, name: str, count: int = 1) -> None:
        self.calls.append((name, count))


class _FakeProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        native_tools: bool,
        effort: bool,
        web_search: bool,
        base_url: str | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._native_tools = native_tools
        self._effort = effort
        self._web_search = web_search
        super().__init__(
            model_name=model_name,
            base_url=base_url,
            data_registry=DataRegistry(),
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def supports_native_tools(self) -> bool:
        return self._native_tools

    @property
    def supports_effort(self) -> bool:
        return self._effort

    @property
    def supports_web_search(self) -> bool:
        return self._web_search

    async def generate(
        self,
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream(
        self,
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ):
        yield StreamChunk(is_final=True)

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse()

    def _create_tool_formatter(self):
        return object()


class _BrokenProvider(_FakeProvider):
    @property
    def supports_native_tools(self) -> bool:
        raise RuntimeError("probe failure")


def test_probe_google_provider_marks_capabilities_supported() -> None:
    registry = DataRegistry()
    probe = CapabilityProbeService(registry)
    provider = _FakeProvider(
        provider_name="google",
        model_name="gemini-2.5-flash-lite",
        native_tools=True,
        effort=True,
        web_search=True,
    )

    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["native_tools"].status == "supported"
    assert snapshot.effective["effort"].status == "supported"
    assert snapshot.effective["web_search"].status == "supported"
    assert snapshot.effective["web_search"].source == "probe"


def test_probe_openai_provider_marks_web_search_unsupported() -> None:
    registry = DataRegistry()
    probe = CapabilityProbeService(registry)
    provider = _FakeProvider(
        provider_name="openai",
        model_name="gpt-4o",
        native_tools=True,
        effort=False,
        web_search=True,
    )

    snapshot = probe.probe_provider(provider, trigger="model_switch")

    assert snapshot.effective["native_tools"].status == "supported"
    assert snapshot.effective["effort"].status == "unsupported"
    assert snapshot.effective["web_search"].status == "unsupported"
    assert (
        "openai_web_search_not_integrated_runtime"
        in snapshot.effective["web_search"].reason
    )


def test_probe_openai_compatible_web_search_unsupported_by_default() -> None:
    registry = DataRegistry()
    counters = _CounterRecorder()
    probe = CapabilityProbeService(registry, observability=counters)
    provider = _FakeProvider(
        provider_name="openai_compatible",
        model_name="local-model",
        native_tools=True,
        effort=False,
        web_search=False,
    )

    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["web_search"].status == "unsupported"
    assert ("probe_successes", 1) in counters.calls


def test_probe_failure_is_non_blocking_and_records_failure_counter() -> None:
    registry = DataRegistry()
    counters = _CounterRecorder()
    probe = CapabilityProbeService(registry, observability=counters)
    provider = _BrokenProvider(
        provider_name="google",
        model_name="gemini-2.5-flash-lite",
        native_tools=True,
        effort=True,
        web_search=True,
    )

    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["native_tools"].status in {
        "supported",
        "unsupported",
        "unknown",
    }
    assert ("probe_failures", 1) in counters.calls


def test_probe_azure_model_mode_none_marks_web_search_unsupported() -> None:
    registry = DataRegistry()
    probe = CapabilityProbeService(registry)
    provider = _FakeProvider(
        provider_name="azure",
        model_name="azure-kimi-k2.5",
        native_tools=True,
        effort=False,
        web_search=True,
    )
    provider.client = object()

    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["web_search"].status == "unsupported"
    assert (
        "azure_chat_completions_web_search_contract_unavailable"
        in snapshot.effective["web_search"].reason
    )


def test_probe_azure_model_mode_responses_api_marks_web_search_supported() -> None:
    registry = DataRegistry()
    probe = CapabilityProbeService(registry)
    provider = _FakeProvider(
        provider_name="azure",
        model_name="azure-gpt-4.1-mini",
        native_tools=True,
        effort=False,
        web_search=True,
    )
    provider.api_surface = "responses_api"
    provider.client = type(
        "_Client",
        (),
        {
            "responses": type("_Responses", (), {"create": staticmethod(lambda **_: None)})()
        },
    )()
    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["web_search"].status == "supported"
    assert (
        "azure_responses_api_available_runtime"
        in snapshot.effective["web_search"].reason
    )


def test_probe_azure_chat_completions_marks_web_search_supported_with_contract() -> None:
    registry = DataRegistry()
    probe = CapabilityProbeService(registry)
    provider = _FakeProvider(
        provider_name="azure",
        model_name="azure-gpt-4.1-mini",
        native_tools=True,
        effort=False,
        web_search=True,
    )
    provider.api_surface = "chat_completions"
    provider._azure_chat_web_search_contract_available = lambda: True
    provider.client = type(
        "_Client",
        (),
        {
            "chat": type(
                "_Chat",
                (),
                {
                    "completions": type(
                        "_Completions", (), {"create": staticmethod(lambda **_: None)}
                    )()
                },
            )()
        },
    )()

    snapshot = probe.probe_provider(provider, trigger="session_start")

    assert snapshot.effective["web_search"].status == "supported"
    assert (
        "azure_chat_completions_web_search_available_runtime"
        in snapshot.effective["web_search"].reason
    )
