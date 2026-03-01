"""
OpenAI-Compatible Provider — adapter for Ollama, LM Studio, vLLM, etc.

Uses the ``openai`` SDK pointed at a custom ``base_url``.  Defaults to
XML tool prompting (no native FC) unless ``native_tools=True``.

Registered via TOML::

    [providers.local_ollama]
    adapter_type = "openai_compatible"
    base_url = "http://localhost:11434/v1"
    models = ["llama-3-8b", "codestral"]
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.providers.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.providers.models import (
    LLMResponse,
    StreamChunk,
    StopReason,
    ToolCall,
    ToolCallMode,
)
from agent_cli.providers.xml_formatter import XMLToolFormatter

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseLLMProvider):
    """Adapter for any OpenAI-compatible API endpoint.

    Works with Ollama, LM Studio, vLLM, LocalAI, and any service
    that exposes the ``/v1/chat/completions`` endpoint.

    By default uses XML tool prompting.  Set ``native_tools=True``
    if the endpoint supports OpenAI-style function calling.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: str = "http://localhost:11434/v1",
        native_tools: bool = False,
    ) -> None:
        self._native_tools = native_tools
        super().__init__(model_name, api_key, base_url)

        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(
            api_key=api_key or "ollama",  # Many local servers don't need a key
            base_url=base_url,
        )

        # Streaming buffer
        self._buffered_text: List[str] = []
        self._buffered_usage: Dict[str, int] = {"input": 0, "output": 0}

    @property
    def provider_name(self) -> str:
        return getattr(self, "_runtime_provider_name", "openai_compatible")

    @property
    def supports_native_tools(self) -> bool:
        return self._native_tools

    # ── generate() ───────────────────────────────────────────────

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # For XML mode: inject tools into the system prompt, clear tools param
        if tools and not self._native_tools:
            tool_text = self._tool_formatter.format_for_prompt_injection(tools)
            context = self._inject_tools_into_system_prompt(context, tool_text)
            tools = None

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": context,
            "max_tokens": max_tokens,
        }
        if tools and self._native_tools:
            # Re-use OpenAI tool format for compatible endpoints
            from agent_cli.providers.provider.openai_provider import OpenAIToolFormatter

            kwargs["tools"] = OpenAIToolFormatter().format_for_native_fc(tools)

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract usage if available (some local servers don't report it)
        input_tokens = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
        output_tokens = getattr(response.usage, "completion_tokens", 0) if response.usage else 0

        # Parse native tool calls if applicable
        tool_calls = []
        if self._native_tools and choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                        mode=ToolCallMode.NATIVE,
                        native_call_id=tc.id,
                    )
                )

        return LLMResponse(
            text_content=choice.message.content or "",
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.XML,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,  # Local models are typically free
            model=self.model_name,
            provider="openai_compatible",
        )

    # ── stream() ─────────────────────────────────────────────────

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        self._buffered_text = []
        self._buffered_usage = {"input": 0, "output": 0}

        if tools and not self._native_tools:
            tool_text = self._tool_formatter.format_for_prompt_injection(tools)
            context = self._inject_tools_into_system_prompt(context, tool_text)
            tools = None

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": context,
            "max_tokens": max_tokens,
            "stream": True,
        }

        response = await self.client.chat.completions.create(**kwargs)
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                self._buffered_text.append(text)
                yield StreamChunk(text=text)

            # Some local servers report usage on the last chunk
            if chunk.usage:
                self._buffered_usage["input"] = chunk.usage.prompt_tokens or 0
                self._buffered_usage["output"] = chunk.usage.completion_tokens or 0

        yield StreamChunk(
            is_final=True,
            usage={
                "input_tokens": self._buffered_usage["input"],
                "output_tokens": self._buffered_usage["output"],
            },
        )

    def get_buffered_response(self) -> LLMResponse:
        text = "".join(self._buffered_text)
        return LLMResponse(
            text_content=text,
            tool_calls=[],  # XML parsing is done by the Schema Validator
            tool_mode=ToolCallMode.XML,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=0.0,
            model=self.model_name,
            provider="openai_compatible",
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return XMLToolFormatter()

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0  # Local models are typically free
