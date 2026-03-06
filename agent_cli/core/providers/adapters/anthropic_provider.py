"""
Anthropic Provider — adapter for Claude 4.6 (Sonnet/Opus) via ``anthropic`` SDK.

Supports native tool_use blocks and streaming.  Anthropic requires the
system message to be passed separately from the messages array.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.core.infra.config.config_models import EffortLevel, normalize_effort
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.core.providers.base.json_formatter import JSONToolFormatter
from agent_cli.core.providers.base.models import (
    LLMResponse,
    ProviderRequestOptions,
    StopReason,
    StreamChunk,
    ToolCall,
    ToolCallMode,
)

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
        return JSONToolFormatter().format_for_prompt_injection(tools)


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
        *,
        data_registry: DataRegistry,
    ) -> None:
        super().__init__(
            model_name,
            api_key,
            base_url,
            data_registry=data_registry,
        )

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

    @property
    def supports_effort(self) -> bool:
        return True

    @property
    def supports_web_search(self) -> bool:
        return True

    def resolve_effective_effort(
        self,
        effort: str | EffortLevel | None,
    ) -> EffortLevel:
        """Anthropic supports max level ONLY on Opus 4.6 models."""
        requested = normalize_effort(effort)
        if requested == EffortLevel.MAX and "opus-4-6" not in self.model_name:
            return EffortLevel.HIGH
        return super().resolve_effective_effort(requested)

    # ── generate() ───────────────────────────────────────────────

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        system_msg, chat_history = self._split_system_message(context)

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }

        # Apply effort hint if supported and provided
        anthropic_effort = self._resolve_anthropic_effort(effort)
        if anthropic_effort:
            kwargs["output_config"] = {"effort": anthropic_effort}

        if system_msg:
            kwargs["system"] = system_msg
        request_tools: List[Dict[str, Any]] = []
        if tools:
            request_tools.extend(self._tool_formatter.format_for_native_fc(tools))
        if self._web_search_enabled(request_options):
            request_tools.append(self._build_web_search_tool())
        if request_tools:
            kwargs["tools"] = request_tools

        try:
            response = await self.client.messages.create(**kwargs)
        except Exception as exc:
            # Some Anthropic deployments reject provider-managed web search
            # despite static capability metadata. Retry once without it.
            if not self._should_retry_without_web_search(exc, request_tools):
                raise
            logger.warning(
                "Anthropic web_search tool rejected by model '%s'; "
                "retrying request without provider-managed web search.",
                self.model_name,
            )
            kwargs = self._without_provider_web_search(kwargs)
            response = await self.client.messages.create(**kwargs)
        return self._normalize(response)

    def _normalize(self, response: Any) -> LLMResponse:
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                if self._is_provider_managed_tool(block.name):
                    continue
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
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
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

        # Apply effort hint if supported and provided
        anthropic_effort = self._resolve_anthropic_effort(effort)
        if anthropic_effort:
            kwargs["output_config"] = {"effort": anthropic_effort}

        if system_msg:
            kwargs["system"] = system_msg
        request_tools: List[Dict[str, Any]] = []
        if tools:
            request_tools.extend(self._tool_formatter.format_for_native_fc(tools))
        if self._web_search_enabled(request_options):
            request_tools.append(self._build_web_search_tool())
        if request_tools:
            kwargs["tools"] = request_tools

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
                        if self._is_provider_managed_tool(event.content_block.name):
                            continue
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

    def _web_search_enabled(
        self,
        request_options: ProviderRequestOptions | None,
    ) -> bool:
        return bool(request_options is not None and request_options.web_search_enabled)

    def _build_web_search_tool(self) -> Dict[str, Any]:
        defaults = self._data_registry.get_web_search_defaults()

        # Resolve tool_type: Model-specific > Hardcoded Fallback.
        tool_type = "web_search_20260209"

        # 1. Try model capabilities
        caps = self._data_registry.get_model_capabilities(self.model_name)
        if caps and caps.web_search and caps.web_search.tool_type:
            tool_type = caps.web_search.tool_type

        max_uses_raw = defaults.get("max_uses", 10)
        try:
            max_uses = max(int(max_uses_raw), 1)
        except (TypeError, ValueError):
            max_uses = 10

        tool: Dict[str, Any] = {
            "type": tool_type,
            "name": "web_search",
            "max_uses": max_uses,
        }
        # Anthropic Claude 4.5 family expects web_search callers constrained
        # to "direct" for this API/tool variant.
        allowed_callers_raw = defaults.get("allowed_callers", ["direct"])
        if isinstance(allowed_callers_raw, list):
            allowed_callers = [
                str(caller).strip()
                for caller in allowed_callers_raw
                if str(caller).strip()
            ]
            if allowed_callers:
                tool["allowed_callers"] = allowed_callers
        allowed_domains = defaults.get("allowed_domains", [])
        if isinstance(allowed_domains, list):
            normalized = [str(domain).strip() for domain in allowed_domains if domain]
            if normalized:
                tool["allowed_domains"] = normalized
        return tool

    @staticmethod
    def _without_provider_web_search(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Return request kwargs with provider-managed web search tools removed."""
        cleaned = dict(kwargs)
        tools = cleaned.get("tools")
        if not isinstance(tools, list):
            return cleaned

        filtered = [
            tool
            for tool in tools
            if not AnthropicProvider._is_web_search_tool_definition(tool)
        ]
        if filtered:
            cleaned["tools"] = filtered
        else:
            cleaned.pop("tools", None)
        return cleaned

    @staticmethod
    def _is_web_search_tool_definition(tool: Any) -> bool:
        if not isinstance(tool, dict):
            return False
        tool_type = str(tool.get("type", "")).strip().lower()
        tool_name = str(tool.get("name", "")).strip().lower()
        return tool_name == "web_search" or tool_type.startswith("web_search_")

    @classmethod
    def _should_retry_without_web_search(
        cls,
        error: Exception,
        request_tools: List[Dict[str, Any]],
    ) -> bool:
        if not request_tools:
            return False
        if not any(cls._is_web_search_tool_definition(tool) for tool in request_tools):
            return False

        # Retry only for known web-search contract rejections.
        message = str(error).lower()
        return (
            "invalid_request_error" in message
            and "web_search" in message
            and (
                "allowed_callers" in message
                or "field required" in message
                or "tools." in message
            )
        )

    @staticmethod
    def _is_provider_managed_tool(tool_name: Any) -> bool:
        return str(tool_name).strip().lower() == "web_search"

    @staticmethod
    def _map_stop_reason(reason: Optional[str]) -> StopReason:
        mapping = {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop_sequence": StopReason.STOP_SEQUENCE,
        }
        return mapping.get(reason or "", StopReason.END_TURN)

    def _resolve_anthropic_effort(self, effort: str | EffortLevel | None) -> Optional[str]:
        """Resolve our canonical effort level to Anthropic's API string."""
        normalized = normalize_effort(effort)
        if normalized == EffortLevel.AUTO:
            return None

        # Image reference mapping
        mapping = {
            EffortLevel.MINIMAL: "low",
            EffortLevel.LOW: "low",
            EffortLevel.MEDIUM: "medium",
            EffortLevel.HIGH: "high",
            EffortLevel.MAX: "max",
        }
        return mapping.get(normalized)
