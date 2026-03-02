"""
Anthropic Provider — adapter for Claude 4.6 (Sonnet/Opus) via ``anthropic`` SDK.

Supports native tool_use blocks and streaming.  Anthropic requires the
system message to be passed separately from the messages array.
"""

from __future__ import annotations

import importlib
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


class AnthropicToolFormatter(BaseToolFormatter):
    """Converts internal tool definitions to Anthropic tool format."""

    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {}),
            }
            for t in tools
        ]

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        return XMLToolFormatter().format_for_prompt_injection(tools)


# ══════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════


class AnthropicProvider(BaseLLMProvider):
    """Adapter for the Anthropic API (Claude 4.6 Sonnet/Opus, etc.).

    Uses the ``anthropic`` Python SDK with ``AsyncAnthropic``.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(model_name, api_key, base_url)

        anthropic_mod = importlib.import_module("anthropic")
        async_anthropic_cls = getattr(anthropic_mod, "AsyncAnthropic")

        kwargs: Dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        self.client = async_anthropic_cls(**kwargs)

        # Streaming buffer
        self._buffered_text: List[str] = []
        self._buffered_tool_calls: List[ToolCall] = []
        self._buffered_usage: Dict[str, int] = {"input": 0, "output": 0}
        self._buffered_stop_reason: StopReason = StopReason.END_TURN

    @property
    def provider_name(self) -> str:
        return "anthropic"

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
        system_msg, chat_history = self._split_system_message(context)

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)

        response = await self.client.messages.create(**kwargs)
        return self._normalize(response)

    def _normalize(self, response: Any) -> LLMResponse:
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        tool_name=block.name,
                        arguments=block.input,
                        mode=ToolCallMode.NATIVE,
                        native_call_id=block.id,
                    )
                )

        stop = self._map_stop_reason(response.stop_reason)
        cost = self.estimate_cost(
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return LLMResponse(
            text_content="\n".join(text_parts),
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="anthropic",
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

        system_msg, chat_history = self._split_system_message(context)

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        text = event.delta.text
                        self._buffered_text.append(text)
                        yield StreamChunk(text=text)

                    # input_json_delta is tool call arg accumulation — don't stream

                elif event.type == "content_block_stop":
                    # Check if this was a tool_use block
                    if (
                        hasattr(event, "content_block")
                        and event.content_block.type == "tool_use"
                    ):
                        self._buffered_tool_calls.append(
                            ToolCall(
                                tool_name=event.content_block.name,
                                arguments=event.content_block.input,
                                mode=ToolCallMode.NATIVE,
                                native_call_id=event.content_block.id,
                            )
                        )

                elif event.type == "message_delta":
                    if hasattr(event, "usage") and event.usage:
                        self._buffered_usage["output"] = event.usage.output_tokens
                    if hasattr(event, "delta") and hasattr(event.delta, "stop_reason"):
                        self._buffered_stop_reason = self._map_stop_reason(
                            event.delta.stop_reason
                        )

            # Capture input tokens from the final message
            final = await stream.get_final_message()
            self._buffered_usage["input"] = final.usage.input_tokens

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
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=cost,
            model=self.model_name,
            provider="anthropic",
            stop_reason=self._buffered_stop_reason,
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return AnthropicToolFormatter()

    @staticmethod
    def _map_stop_reason(reason: Optional[str]) -> StopReason:
        mapping = {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop_sequence": StopReason.STOP_SEQUENCE,
        }
        return mapping.get(reason or "", StopReason.END_TURN)
