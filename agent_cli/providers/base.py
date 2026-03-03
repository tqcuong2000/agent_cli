"""
Provider Abstractions — ``BaseLLMProvider`` and ``BaseToolFormatter``.

These ABCs define the contract that every concrete adapter (OpenAI,
Anthropic, Google, etc.) must fulfill.  The Agent loop and Schema
Validator program against these interfaces — never against a specific
vendor SDK.

``safe_generate()`` wraps the raw ``generate()`` with the retry engine
from Phase 1 (error classification → exponential backoff).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.core.error_handler.errors import (
    AgentCLIError,
    AuthenticationError,
    ContextLengthExceededError,
    LLMOverloadError,
    LLMRateLimitError,
    LLMTransientError,
)
from agent_cli.core.error_handler.retry import retry_with_backoff
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.logging import get_observability
from agent_cli.core.tracing import start_span
from agent_cli.data import DataRegistry
from agent_cli.providers.cost import estimate_cost
from agent_cli.providers.models import LLMResponse, StreamChunk

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# BaseToolFormatter (2.1.4)
# ══════════════════════════════════════════════════════════════════════


class BaseToolFormatter(ABC):
    """Converts internal tool definitions into provider-specific formats.

    Each concrete provider creates its own formatter via
    ``_create_tool_formatter()``.
    """

    @abstractmethod
    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        """Convert tool definitions to the provider's native FC format.

        Input:
            List of dicts with keys: name, description, parameters
            (JSON Schema from Pydantic).

        Output:
            Provider-specific format (e.g. OpenAI tools array).
        """
        ...

    @abstractmethod
    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        """Convert tool definitions to a text block for system prompt injection.

        Used for providers that don't support native function calling
        (Ollama, local models).
        """
        ...


# ══════════════════════════════════════════════════════════════════════
# BaseLLMProvider (2.1.1)
# ══════════════════════════════════════════════════════════════════════


class BaseLLMProvider(ABC):
    """Abstract adapter for communicating with different LLM backends.

    Responsibilities:
        - Payload translation (internal messages → provider API format)
        - Tool mode selection (native FC or prompt injection)
        - Response normalization (provider response → ``LLMResponse``)
        - Cost estimation

    Does NOT handle:
        - Retries (handled by ``safe_generate()`` using the retry engine)
        - Token counting (handled by the Memory Manager in Phase 3)
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self._data_registry = data_registry
        self._runtime_provider_name: Optional[str] = None
        self._tool_formatter = self._create_tool_formatter()

    # ── Abstract Properties ──────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (e.g. 'openai', 'anthropic', 'google')."""
        ...

    @property
    @abstractmethod
    def supports_native_tools(self) -> bool:
        """Whether this provider supports native function calling.

        If True:  tools sent as API parameters, responses contain
                  structured tool calls (``ToolCallMode.NATIVE``).
        If False: tools injected into the system prompt, responses use
                  XML ``<action>`` tags (``ToolCallMode.XML``).
        """
        ...

    # ── Abstract Methods ─────────────────────────────────────────

    @abstractmethod
    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make a single API call and return a normalized ``LLMResponse``.

        Args:
            context:    Conversation history (list of message dicts).
            tools:      Tool definitions (the provider decides HOW to deliver).
            max_tokens: Max tokens for the response.

        Raises:
            AgentCLIError subclass on any API error.
        """
        ...

    @abstractmethod
    def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Yield ``StreamChunk`` objects as content arrives from the API.

        Streaming strategy:
            - Text content (including ``<thinking>``) is yielded chunk by chunk.
            - Tool calls are buffered internally — NOT streamed.
            - After the stream ends, call ``get_buffered_response()``
              for the complete ``LLMResponse``.
        """
        ...

    @abstractmethod
    def get_buffered_response(self) -> LLMResponse:
        """Return the finalized ``LLMResponse`` after streaming completes.

        Contains full text, tool calls, token usage, and cost.
        """
        ...

    @abstractmethod
    def _create_tool_formatter(self) -> BaseToolFormatter:
        """Create the provider-specific tool formatter."""
        ...

    # ── Cost Estimation ──────────────────────────────────────────

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD for a single API call.

        Override in subclasses for custom pricing logic.
        """
        return estimate_cost(
            self.model_name,
            input_tokens,
            output_tokens,
            data_registry=self._data_registry,
        )

    # ── safe_generate: Retry Wrapper ─────────────────────────────

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        task_id: str = "",
        event_bus: Optional[AbstractEventBus] = None,
    ) -> LLMResponse:
        """Wrap ``generate()`` with the Phase 1 retry engine.

        Error classification:
            - 429 / rate_limit → ``LLMRateLimitError`` (TRANSIENT, retry)
            - 500 / 503 / overloaded → ``LLMOverloadError`` (TRANSIENT, retry)
            - 401 / 403 / invalid_api_key → ``AuthenticationError`` (FATAL, no retry)
            - context_length / too many tokens → ``ContextLengthExceededError`` (RECOVERABLE)
            - Everything else → ``LLMTransientError`` (TRANSIENT, retry)
        """

        async def _attempt() -> LLMResponse:
            try:
                return await self.generate(context, tools, max_tokens)
            except AgentCLIError:
                raise  # Already classified
            except Exception as e:
                raise self._classify_error(e) from e

        retry_defaults = (
            self._data_registry.get_retry_defaults()
            if self._data_registry is not None
            else {}
        )
        retries = int(
            max_retries
            if max_retries is not None
            else retry_defaults.get("llm_max_retries", 3)
        )
        base = float(
            base_delay
            if base_delay is not None
            else retry_defaults.get("llm_retry_base_delay", 1.0)
        )
        delay_cap = float(
            max_delay
            if max_delay is not None
            else retry_defaults.get("llm_retry_max_delay", 30.0)
        )
        span = start_span("llm_call", task_id=task_id)
        try:
            response = await retry_with_backoff(
                _attempt,
                max_retries=retries,
                base_delay=base,
                max_delay=delay_cap,
                task_id=task_id,
                event_bus=event_bus,
            )
        except Exception:
            timing = span.finish()
            logger.error(
                "LLM call failed",
                extra={
                    "source": self.provider_name,
                    "task_id": task_id,
                    "span_id": timing["span_id"],
                    "span_type": timing["span_type"],
                    "data": {
                        "model": self.model_name,
                        "duration_ms": timing["duration_ms"],
                    },
                },
                exc_info=True,
            )
            raise

        timing = span.finish()
        observability = get_observability()
        if observability is not None:
            observability.record_llm_call(
                task_id=task_id,
                model=self.model_name,
                provider=self.provider_name,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                duration_ms=int(timing["duration_ms"]),
                cost_usd=response.cost_usd,
            )
        return response

    # ── Error Classification ─────────────────────────────────────

    def _classify_error(self, error: Exception) -> AgentCLIError:
        """Convert provider-specific exceptions into the error taxonomy."""
        msg = str(error).lower()

        if "rate_limit" in msg or "429" in msg:
            retry_after = self._extract_retry_after(error)
            return LLMRateLimitError(
                f"Rate limited by {self.provider_name}: {error}",
                retry_after=retry_after,
            )
        elif "500" in msg or "503" in msg or "overloaded" in msg or "529" in msg:
            return LLMOverloadError(f"Server error from {self.provider_name}: {error}")
        elif (
            "401" in msg
            or "403" in msg
            or "invalid_api_key" in msg
            or "authentication" in msg
        ):
            return AuthenticationError(
                f"Authentication failed for {self.provider_name}: {error}",
                user_message=f"API key for {self.provider_name} is invalid or expired. "
                f"Check your .env file or keyring.",
            )
        elif (
            "context_length" in msg
            or "too many tokens" in msg
            or "maximum context" in msg
        ):
            return ContextLengthExceededError(
                f"Context too long for {self.model_name}: {error}",
                user_message="The conversation is too long for this model. "
                "Context compaction will be triggered.",
            )
        else:
            return LLMTransientError(
                f"Provider error from {self.provider_name}: {error}"
            )

    @staticmethod
    def _extract_retry_after(error: Exception) -> Optional[float]:
        """Try to extract a retry-after value from the exception."""
        # Many SDKs attach retry_after or response headers
        err_any: Any = error
        retry_after = getattr(err_any, "retry_after", None)
        if retry_after is not None:
            return float(retry_after)

        response = getattr(err_any, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            val = headers.get("retry-after")
            if val:
                try:
                    return float(val)
                except ValueError:
                    pass
        return None

    # ── Utility ──────────────────────────────────────────────────

    @staticmethod
    def _split_system_message(
        context: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Separate the system message from chat history.

        Useful for Anthropic and other providers that require the
        system message as a separate parameter. Mid-conversation system
        messages are converted to user messages so order is preserved.
        """
        system = ""
        messages: List[Dict[str, Any]] = []
        for msg in context:
            if msg.get("role") == "system":
                if not messages:
                    if system:
                        system += "\n\n" + msg.get("content", "")
                    else:
                        system = msg.get("content", "")
                else:
                    messages.append({"role": "user", "content": f"[System: {msg.get('content', '')}]"})
            else:
                messages.append(msg)
        return system, messages

    @staticmethod
    def _inject_tools_into_system_prompt(
        context: List[Dict[str, Any]],
        tool_text: str,
    ) -> List[Dict[str, Any]]:
        """Append tool definitions to the system prompt for XML mode."""
        modified = []
        for msg in context:
            if msg.get("role") == "system":
                modified.append(
                    {
                        "role": "system",
                        "content": msg["content"] + "\n\n" + tool_text,
                    }
                )
            else:
                modified.append(msg)
        return modified
