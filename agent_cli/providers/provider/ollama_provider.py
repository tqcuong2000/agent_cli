"""
Ollama Provider — adapter for local LLMs via the ``ollama`` library.

Supports both native function calling (for capable models) and XML tool prompting.
Uses ``ollama.AsyncClient`` for all operations.
"""

from __future__ import annotations

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


class OllamaToolFormatter(BaseToolFormatter):
    """Converts internal tool definitions to Ollama's native tool format."""

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
        return XMLToolFormatter().format_for_prompt_injection(tools)


# ══════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════


class OllamaProvider(BaseLLMProvider):
    """Adapter for the Ollama local API.

    Requires the ``ollama`` Python library and a running Ollama server.
    Defaults to XML tool prompting unless ``native_tools=True`` is passed.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = "http://localhost:11434",
        native_tools: bool = False,
    ) -> None:
        super().__init__(model_name, api_key, base_url)
        self._native_tools = native_tools

        from ollama import AsyncClient
        self.client = AsyncClient(host=base_url)

        # Streaming buffer
        self._buffered_text: List[str] = []
        self._buffered_tool_calls: List[ToolCall] = []
        self._buffered_usage: Dict[str, int] = {"input": 0, "output": 0}

    @property
    def provider_name(self) -> str:
        return getattr(self, "_runtime_provider_name", "ollama")

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
        # Resolve tool strategy
        native_tools = None
        if tools and self._native_tools:
            native_tools = self._tool_formatter.format_for_native_fc(tools)
        elif tools:
            # Inject tools into system prompt for XML mode
            tool_text = self._tool_formatter.format_for_prompt_injection(tools)
            context = self._inject_tools_into_system_prompt(context, tool_text)

        options = {"num_predict": max_tokens}
        
        response = await self.client.chat(
            model=self.model_name,
            messages=context,
            tools=native_tools,
            options=options,
        )

        return self._normalize(response)

    def _normalize(self, response: Any) -> LLMResponse:
        msg = response.message
        
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.function.name,
                        arguments=tc.function.arguments or {},
                        mode=ToolCallMode.NATIVE,
                        native_call_id="", # Ollama doesn't always provide IDs
                    )
                )

        # Ollama usage names vary; we try to normalize
        input_tokens = response.get("prompt_eval_count", 0)
        output_tokens = response.get("eval_count", 0)

        return LLMResponse(
            text_content=msg.content or "",
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.XML,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0, # Local is free
            model=self.model_name,
            provider="ollama",
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

        # Resolve tool strategy
        native_tools = None
        if tools and self._native_tools:
            native_tools = self._tool_formatter.format_for_native_fc(tools)
        elif tools:
            tool_text = self._tool_formatter.format_for_prompt_injection(tools)
            context = self._inject_tools_into_system_prompt(context, tool_text)

        options = {"num_predict": max_tokens}

        async for chunk in await self.client.chat(
            model=self.model_name,
            messages=context,
            tools=native_tools,
            options=options,
            stream=True,
        ):
            if chunk.message.content:
                text = chunk.message.content
                self._buffered_text.append(text)
                yield StreamChunk(text=text)

            if chunk.message.tool_calls:
                for tc in chunk.message.tool_calls:
                    self._buffered_tool_calls.append(
                        ToolCall(
                            tool_name=tc.function.name,
                            arguments=tc.function.arguments or {},
                            mode=ToolCallMode.NATIVE,
                            native_call_id="",
                        )
                    )

            if chunk.get("done", False):
                self._buffered_usage["input"] = chunk.get("prompt_eval_count", 0)
                self._buffered_usage["output"] = chunk.get("eval_count", 0)

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
            tool_calls=self._buffered_tool_calls,
            tool_mode=ToolCallMode.NATIVE if self._buffered_tool_calls else ToolCallMode.XML,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=0.0,
            model=self.model_name,
            provider="ollama",
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return OllamaToolFormatter()

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0
