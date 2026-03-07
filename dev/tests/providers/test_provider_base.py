"""
Unit tests for Provider abstractions and data models (Sub-Phase 2.1).

Covers:
- Data models (LLMRequest, LLMResponse, ToolCall, StreamChunk)
- BaseLLMProvider wrapper logic (safe_generate, _classify_error)
- Error taxonomy integration with the provider layer
"""

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest

from agent_cli.core.infra.events.errors import (
    AuthenticationError,
    ContextLengthExceededError,
    LLMOverloadError,
    LLMRateLimitError,
    LLMTransientError,
)
from agent_cli.core.infra.config.config_models import EffortLevel
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.core.providers.base.models import (
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    ProviderRequestOptions,
    StopReason,
    StreamChunk,
    ToolCall,
    ToolCallMode,
)

# ── Mocks ────────────────────────────────────────────────────────────


class MockToolFormatter(BaseToolFormatter):
    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        return {"native": tools}

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        return f"<tools>{len(tools)}</tools>"


class MockProvider(BaseLLMProvider):
    def __init__(
        self,
        model_name: str,
        simulate_error: Optional[Exception] = None,
        supports_fc: bool = True,
    ):
        super().__init__(model_name, data_registry=DataRegistry())
        self.simulate_error = simulate_error
        self._supports_fc = supports_fc
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return self._supports_fc

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        _ = effort
        _ = request_options
        self.call_count += 1
        if self.simulate_error:
            raise self.simulate_error

        return LLMResponse(
            text_content="Success",
            model=self.model_name,
            provider=self.provider_name,
        )

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        _ = effort
        _ = request_options
        yield StreamChunk(text="Chunk")

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="Buffered", provider="mock")

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return MockToolFormatter()


# ── Model Tests ──────────────────────────────────────────────────────


def test_llm_request_to_message_dicts():
    """Verify LLMRequest accurately converts Messages to plain dicts."""
    req = LLMRequest(
        messages=[
            Message(role=MessageRole.USER, content="Hello"),
            Message(role=MessageRole.TOOL, content="Data", tool_call_id="call_abc"),
        ]
    )
    dicts = req.to_message_dicts()

    assert len(dicts) == 2
    assert dicts[0] == {"role": "user", "content": "Hello"}
    assert dicts[1] == {"role": "tool", "content": "Data", "tool_call_id": "call_abc"}


def test_llm_response_properties():
    """Verify computed properties on LLMResponse."""
    r1 = LLMResponse(text_content="I thought about it.", tool_calls=[])
    assert not r1.has_tool_calls
    assert not r1.is_final_answer

    r2 = LLMResponse(
        text_content="Let me fetch that.",
        tool_calls=[ToolCall(tool_name="fetch", arguments={"a": 1})],
    )
    assert r2.has_tool_calls
    assert not r2.is_final_answer

    r3 = LLMResponse(
        text_content='{"title":"Done","thought":"Complete","decision":{"type":"notify_user","message":"42"}}'
    )
    assert not r3.has_tool_calls
    assert r3.is_final_answer


def test_tool_call_mode_normalization():
    """Tool mode normalization should accept canonical values."""
    assert ToolCallMode("PROMPT_JSON") == ToolCallMode.PROMPT_JSON


# ── BaseLLMProvider Classification Tests ─────────────────────────────


def test_error_classification():
    """Verify provider errors are mapped to the correct AgentCLIError tier."""
    provider = MockProvider("test-model")

    # 429 Rate Limit
    err1 = provider._classify_error(Exception("429 Too Many Requests"))
    assert isinstance(err1, LLMRateLimitError)
    assert err1.error_id == "provider.rate_limited"

    # 503 Overload
    err2 = provider._classify_error(ValueError("Provider overloaded 503"))
    assert isinstance(err2, LLMOverloadError)
    assert err2.error_id == "provider.overloaded"

    # 401 Auth
    err3 = provider._classify_error(Exception("401 invalid_api_key"))
    assert isinstance(err3, AuthenticationError)
    assert err3.error_id == "provider.authentication_failed"

    # 400 Context Length
    err4 = provider._classify_error(Exception("maximum context length exceeded"))
    assert isinstance(err4, ContextLengthExceededError)
    assert err4.error_id == "provider.context_length_exceeded"

    # Generic Transient
    err5 = provider._classify_error(ConnectionError("Connection timeout"))
    assert isinstance(err5, LLMTransientError)
    assert err5.error_id == "provider.transient_error"


def test_extract_retry_after():
    """Verify retry-after hint extraction across different mock SDK error shapes."""

    class MockErrorWithProp:
        retry_after = 2.5

    class MockErrorWithHeaders:
        class Response:
            headers = {"retry-after": "5.0"}

        response = Response()

    assert MockProvider._extract_retry_after(MockErrorWithProp()) == 2.5
    assert MockProvider._extract_retry_after(MockErrorWithHeaders()) == 5.0
    assert MockProvider._extract_retry_after(Exception("foo")) is None


# ── BaseLLMProvider safe_generate Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_safe_generate_success():
    """Verify safe_generate returns normally when no error occurs."""
    provider = MockProvider("test-model")
    res = await provider.safe_generate([], max_retries=1)

    assert res.text_content == "Success"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_safe_generate_retries_transient_error():
    """Verify safe_generate retries on transient errors."""

    # We'll raise a 429 on the first call, succeed on the second
    class FlakyProvider(MockProvider):
        async def generate(
            self,
            context,
            tools=None,
            max_tokens=4096,
            effort=None,
            request_options=None,
        ):
            _ = effort
            _ = request_options
            self.call_count += 1
            if self.call_count == 1:
                raise Exception("429 Rate Limit Exceeded")
            return LLMResponse(text_content="Success", provider="flaky")

    provider = FlakyProvider("test-model")

    # We use base_delay=0 to speed up the test
    # but the retry engine enforces min base_delay inside its loop
    # Let's mock asyncio.sleep instead to bypass delays completely
    original_sleep = asyncio.sleep

    async def fast_sleep(sec):
        pass

    asyncio.sleep = fast_sleep

    try:
        res = await provider.safe_generate([], max_retries=2)
        assert res.text_content == "Success"
        assert provider.call_count == 2
    finally:
        asyncio.sleep = original_sleep


@pytest.mark.asyncio
async def test_safe_generate_fails_immediately_on_auth_error():
    """Verify safe_generate hits AuthError and raises WITHOUT retrying."""
    provider = MockProvider(
        "test-model", simulate_error=Exception("401 invalid_api_key")
    )

    with pytest.raises(AuthenticationError):
        await provider.safe_generate([], max_retries=3)

    assert provider.call_count == 1  # Only tried once, no retries


@pytest.mark.asyncio
async def test_safe_generate_forwards_effort():
    """safe_generate should forward effort hint to provider.generate."""

    class EffortCaptureProvider(MockProvider):
        def __init__(self, model_name: str):
            super().__init__(model_name)
            self.last_effort: str | EffortLevel | None = None

        async def generate(
            self,
            context,
            tools=None,
            max_tokens=4096,
            effort=None,
            request_options=None,
        ):
            self.call_count += 1
            self.last_effort = effort
            return LLMResponse(text_content="ok", provider="capture")

    provider = EffortCaptureProvider("test-model")
    await provider.safe_generate([], effort=EffortLevel.HIGH, max_retries=1)
    assert provider.last_effort == EffortLevel.HIGH


# ── Utilities ────────────────────────────────────────────────────────


def test_split_system_message():
    """Verify removing the system message from context."""
    context = [
        {"role": "system", "content": "You are a bot"},
        {"role": "user", "content": "Hi"},
    ]
    sys, msgs = MockProvider._split_system_message(context)

    assert sys == "You are a bot"
    assert msgs == [{"role": "user", "content": "Hi"}]


def test_inject_tools_into_system_prompt():
    """Verify tools are appended to the system message for prompt mode."""
    context = [{"role": "system", "content": "You are a bot"}]
    modified = MockProvider._inject_tools_into_system_prompt(
        context, "<tools>1</tools>"
    )

    assert len(modified) == 1
    assert "You are a bot" in modified[0]["content"]
    assert "<tools>1</tools>" in modified[0]["content"]


def test_resolve_effective_effort_defaults_to_auto_when_unsupported():
    provider = MockProvider("test-model", supports_fc=True)
    assert provider.supports_effort is False
    assert provider.resolve_effective_effort("high") == EffortLevel.AUTO


def test_resolve_effective_effort_keeps_requested_when_supported():
    class EffortProvider(MockProvider):
        @property
        def supports_effort(self) -> bool:
            return True

    provider = EffortProvider("test-model", supports_fc=True)
    assert provider.resolve_effective_effort("high") == EffortLevel.HIGH
