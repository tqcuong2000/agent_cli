"""
Google Provider — adapter for Gemini models via the ``google-genai`` SDK.

Uses the new unified ``google.genai`` package (the ``google-generativeai``
package was deprecated in November 2025).  Supports native function
declarations and streaming.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.data import DataRegistry
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


class GoogleToolFormatter(BaseToolFormatter):
    """Converts internal tool definitions to Google function declaration format."""

    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> List[Any]:
        """Convert to google.genai Tool format.

        Returns a list of function declarations that can be passed
        to the ``tools`` parameter of ``generate_content``.
        """
        types = _google_types_module()

        declarations = []
        for t in tools:
            declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=t.get("parameters", {}),
                )
            )

        return [types.Tool(function_declarations=declarations)]

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        return XMLToolFormatter().format_for_prompt_injection(tools)


# ══════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════


class GoogleProvider(BaseLLMProvider):
    """Adapter for Google Gemini models via the ``google-genai`` SDK.

    Uses the new unified ``google.genai.Client`` for async operations.

    Note: Gemini uses ``user`` and ``model`` roles (not ``assistant``).
    The ``system`` message is passed via ``config.system_instruction``.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        super().__init__(
            model_name,
            api_key,
            base_url,
            data_registry=data_registry,
        )

        genai_mod = importlib.import_module("google.genai")
        client_cls = getattr(genai_mod, "Client")
        self.client = client_cls(api_key=api_key)

        # Streaming buffer
        self._buffered_text: List[str] = []
        self._buffered_tool_calls: List[ToolCall] = []
        self._buffered_usage: Dict[str, int] = {"input": 0, "output": 0}

    @property
    def provider_name(self) -> str:
        return "google"

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
        types = _google_types_module()

        system_msg, gemini_history = self._convert_messages(context)

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system_msg:
            config.system_instruction = system_msg
        if tools:
            config.tools = self._tool_formatter.format_for_native_fc(tools)

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=gemini_history,
            config=config,
        )

        return self._normalize(response)

    def _normalize(self, response: Any) -> LLMResponse:
        text = ""
        tool_calls = []

        for part in response.candidates[0].content.parts:
            if part.text:
                text += part.text
            elif part.function_call:
                fc = part.function_call
                tool_calls.append(
                    ToolCall(
                        tool_name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                        mode=ToolCallMode.NATIVE,
                    )
                )

        input_tokens = response.usage_metadata.prompt_token_count or 0
        output_tokens = response.usage_metadata.candidates_token_count or 0
        cost = self.estimate_cost(input_tokens, output_tokens)

        return LLMResponse(
            text_content=text,
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="google",
        )

    # ── stream() ─────────────────────────────────────────────────

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        types = _google_types_module()

        self._buffered_text = []
        self._buffered_tool_calls = []
        self._buffered_usage = {"input": 0, "output": 0}

        system_msg, gemini_history = self._convert_messages(context)

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system_msg:
            config.system_instruction = system_msg
        if tools:
            config.tools = self._tool_formatter.format_for_native_fc(tools)

        async for chunk in self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=gemini_history,
            config=config,
        ):
            for part in chunk.candidates[0].content.parts:
                if part.text:
                    self._buffered_text.append(part.text)
                    yield StreamChunk(text=part.text)
                elif part.function_call:
                    fc = part.function_call
                    self._buffered_tool_calls.append(
                        ToolCall(
                            tool_name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                            mode=ToolCallMode.NATIVE,
                        )
                    )

            if chunk.usage_metadata:
                self._buffered_usage["input"] = (
                    chunk.usage_metadata.prompt_token_count or 0
                )
                self._buffered_usage["output"] = (
                    chunk.usage_metadata.candidates_token_count or 0
                )

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
            provider="google",
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return GoogleToolFormatter()

    @staticmethod
    def _convert_messages(
        context: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Convert OpenAI-style messages to Gemini format.

        Returns:
            (system_instruction, gemini_history)
        """
        system = ""
        converted = []
        for msg in context:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system = content
            elif role == "assistant":
                converted.append({"role": "model", "parts": [{"text": content}]})
            else:
                converted.append({"role": "user", "parts": [{"text": content}]})

        return system, converted


def _google_types_module() -> Any:
    genai_mod = importlib.import_module("google.genai")
    return getattr(genai_mod, "types")
