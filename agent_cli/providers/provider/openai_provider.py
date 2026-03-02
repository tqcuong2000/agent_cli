"""
OpenAI Provider — adapter for GPT-4.5, GPT-5, o3 via the ``openai`` SDK.

Supports native function calling and streaming.  Tool calls are returned
as structured JSON; text content is streamed chunk-by-chunk.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.providers.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.providers.models import (
    LLMResponse,
    StopReason,
    StreamChunk,
    ToolCall,
    ToolCallMode,
)
from agent_cli.providers.xml_formatter import XMLToolFormatter

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Tool Formatter
# ══════════════════════════════════════════════════════════════════════


class OpenAIToolFormatter(BaseToolFormatter):
    """Converts internal tool definitions to OpenAI function calling format."""

    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {}),
                },
            }
            for t in tools
        ]

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        # Fallback to XML formatter (rarely used for OpenAI)
        return XMLToolFormatter().format_for_prompt_injection(tools)


# ══════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════


class OpenAIProvider(BaseLLMProvider):
    """Adapter for the OpenAI API (GPT-4.5, GPT-5, o3, etc.).

    Uses the ``openai`` Python SDK with ``AsyncOpenAI`` for async operations.
    Supports custom ``base_url`` for Azure OpenAI or proxy endpoints.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(model_name, api_key, base_url)

        openai_mod = importlib.import_module("openai")
        async_openai_cls = getattr(openai_mod, "AsyncOpenAI")

        kwargs: Dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        self.client = async_openai_cls(**kwargs)

        # Streaming buffer (populated during stream(), read by get_buffered_response())
        self._buffered_text: List[str] = []
        self._buffered_tool_calls: List[ToolCall] = []
        self._buffered_usage: Dict[str, int] = {"input": 0, "output": 0}
        self._buffered_stop_reason: StopReason = StopReason.END_TURN

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supports_native_tools(self) -> bool:
        return True

    # ── generate() ───────────────────────────────────────────────

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": context,
        }
        kwargs.update(self._max_tokens_kwargs(max_tokens))
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)

        response = await self.client.chat.completions.create(**kwargs)
        return self._normalize(response)

    def _normalize(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                        mode=ToolCallMode.NATIVE,
                        native_call_id=tc.id,
                    )
                )

        stop = self._map_stop_reason(choice.finish_reason)
        cost = self.estimate_cost(
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

        return LLMResponse(
            text_content=msg.content or "",
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.XML,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="openai",
            stop_reason=stop,
        )

    # ── stream() ─────────────────────────────────────────────────

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        self._buffered_text = []
        self._buffered_tool_calls = []
        self._buffered_usage = {"input": 0, "output": 0}

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": context,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        kwargs.update(self._max_tokens_kwargs(max_tokens))
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)

        # Buffer for assembling streamed tool calls
        tool_call_buffers: Dict[int, Dict[str, Any]] = {}

        response = await self.client.chat.completions.create(**kwargs)
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None

            if delta and delta.content:
                self._buffered_text.append(delta.content)
                yield StreamChunk(text=delta.content)

            # Accumulate tool call deltas
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_buffers:
                        tool_call_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    buf = tool_call_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["arguments"] += tc_delta.function.arguments

            # Capture usage from the final chunk
            if chunk.usage:
                self._buffered_usage["input"] = chunk.usage.prompt_tokens
                self._buffered_usage["output"] = chunk.usage.completion_tokens

            # Capture stop reason
            if chunk.choices and chunk.choices[0].finish_reason:
                self._buffered_stop_reason = self._map_stop_reason(
                    chunk.choices[0].finish_reason
                )

        # Finalize tool calls
        for buf in tool_call_buffers.values():
            try:
                args = json.loads(buf["arguments"]) if buf["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": buf["arguments"]}

            self._buffered_tool_calls.append(
                ToolCall(
                    tool_name=buf["name"],
                    arguments=args,
                    mode=ToolCallMode.NATIVE,
                    native_call_id=buf["id"],
                )
            )

        # Yield final marker
        yield StreamChunk(
            is_final=True,
            usage={
                "input_tokens": self._buffered_usage["input"],
                "output_tokens": self._buffered_usage["output"],
            },
        )

    def get_buffered_response(self) -> LLMResponse:
        text = "".join(self._buffered_text)
        cost = self.estimate_cost(
            self._buffered_usage["input"],
            self._buffered_usage["output"],
        )
        return LLMResponse(
            text_content=text,
            tool_calls=self._buffered_tool_calls,
            tool_mode=ToolCallMode.NATIVE
            if self._buffered_tool_calls
            else ToolCallMode.XML,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=cost,
            model=self.model_name,
            provider="openai",
            stop_reason=self._buffered_stop_reason,
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return OpenAIToolFormatter()

    @staticmethod
    def _map_stop_reason(reason: Optional[str]) -> StopReason:
        mapping = {
            "stop": StopReason.END_TURN,
            "tool_calls": StopReason.TOOL_USE,
            "length": StopReason.MAX_TOKENS,
        }
        return mapping.get(reason or "", StopReason.END_TURN)

    def _max_tokens_kwargs(self, max_tokens: int) -> Dict[str, int]:
        """Return token-limit params compatible with the target model family.

        GPT-5 chat models reject ``max_tokens`` and require
        ``max_completion_tokens``.
        """
        if self.model_name.lower().startswith("gpt-5"):
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens}
