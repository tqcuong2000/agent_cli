"""
OpenAI Provider — adapter for GPT-4.5, GPT-5, o3 via the ``openai`` SDK.

Supports native function calling and streaming.  Tool calls are returned
as structured JSON; text content is streamed chunk-by-chunk.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.core.infra.config.config_models import EffortLevel
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
        # Fallback to prompt-mode JSON formatter (rarely used for OpenAI)
        return JSONToolFormatter().format_for_prompt_injection(tools)


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
        api_surface: str = "chat_completions",
        api_profile: Optional[Dict[str, Any]] = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._api_surface = self._normalize_api_surface(api_surface)
        self._api_profile = deepcopy(api_profile) if isinstance(api_profile, dict) else {}
        super().__init__(
            model_name,
            api_key,
            base_url,
            data_registry=data_registry,
        )

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
        self._azure_responses_web_search_supported: bool | None = None
        self._azure_chat_web_search_supported: bool | None = None

    @property
    def provider_name(self) -> str:
        return self._runtime_provider_name or "openai"

    @property
    def supports_native_tools(self) -> bool:
        return True

    @property
    def api_surface(self) -> str:
        return self._api_surface

    @property
    def supports_web_search(self) -> bool:
        if self.provider_name != "azure":
            return False
        if self._api_surface == "responses_api":
            if self._azure_responses_web_search_supported is False:
                return False
            return bool(
                hasattr(self.client, "responses")
                and hasattr(self.client.responses, "create")
            )
        if self._api_surface == "chat_completions":
            if self._azure_chat_web_search_supported is False:
                return False
            if not self._azure_chat_web_search_contract_available():
                return False
            return bool(
                hasattr(self.client, "chat")
                and hasattr(self.client.chat, "completions")
                and hasattr(self.client.chat.completions, "create")
            )
        return False

    # ── generate() ───────────────────────────────────────────────

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        _ = effort
        azure_web_search_contract = self._resolve_azure_web_search_contract(
            request_options=request_options
        )
        if azure_web_search_contract:
            try:
                if azure_web_search_contract == "responses_api":
                    response = await self._generate_with_azure_web_search(
                        context=context,
                        max_tokens=max_tokens,
                    )
                    success_reason = "azure_responses_api_runtime_success"
                else:
                    response = await self._generate_with_azure_chat_web_search(
                        context=context,
                        max_tokens=max_tokens,
                    )
                    success_reason = "azure_chat_completions_web_search_runtime_success"
                self._save_runtime_web_search_observation(
                    status="supported",
                    reason=success_reason,
                )
                return response
            except Exception as exc:
                if (
                    azure_web_search_contract == "responses_api"
                    and self._is_responses_api_unsupported_error(exc)
                ):
                    self._azure_responses_web_search_supported = False
                    self._save_runtime_web_search_observation(
                        status="unsupported",
                        reason="azure_responses_api_runtime_rejected",
                    )
                    logger.warning(
                        "Azure model '%s' does not support Responses API web search; "
                        "falling back to chat.completions.",
                        self.model_name,
                    )
                elif (
                    azure_web_search_contract == "chat_completions"
                    and self._is_chat_completions_web_search_unsupported_error(exc)
                ):
                    self._azure_chat_web_search_supported = False
                    self._save_runtime_web_search_observation(
                        status="unsupported",
                        reason="azure_chat_completions_web_search_runtime_rejected",
                    )
                    logger.warning(
                        "Azure model '%s' does not support chat.completions web search; "
                        "falling back to regular chat.completions.",
                        self.model_name,
                    )
                else:
                    raise
                if self._user_requested_web_search(context):
                    return self._build_web_search_unavailable_response(exc)

        chat_context = context
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_context,
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
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.PROMPT_JSON,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider=self.provider_name,
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
        _ = effort
        azure_web_search_contract = self._resolve_azure_web_search_contract(
            request_options=request_options
        )
        if azure_web_search_contract:
            try:
                # Keep implementation simple and stable: non-stream responses API call,
                # then emit as one text chunk followed by final usage.
                if azure_web_search_contract == "responses_api":
                    response = await self._generate_with_azure_web_search(
                        context=context,
                        max_tokens=max_tokens,
                    )
                    success_reason = "azure_responses_api_runtime_success"
                else:
                    response = await self._generate_with_azure_chat_web_search(
                        context=context,
                        max_tokens=max_tokens,
                    )
                    success_reason = "azure_chat_completions_web_search_runtime_success"
                self._save_runtime_web_search_observation(
                    status="supported",
                    reason=success_reason,
                )
                self._buffered_text = (
                    [response.text_content] if response.text_content else []
                )
                self._buffered_tool_calls = []
                self._buffered_usage = {
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                }
                self._buffered_stop_reason = response.stop_reason
                if response.text_content:
                    yield StreamChunk(text=response.text_content)
                yield StreamChunk(
                    is_final=True,
                    usage={
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                    },
                )
                return
            except Exception as exc:
                if (
                    azure_web_search_contract == "responses_api"
                    and self._is_responses_api_unsupported_error(exc)
                ):
                    self._azure_responses_web_search_supported = False
                    self._save_runtime_web_search_observation(
                        status="unsupported",
                        reason="azure_responses_api_runtime_rejected",
                    )
                    logger.warning(
                        "Azure model '%s' does not support Responses API web search; "
                        "falling back to chat.completions stream.",
                        self.model_name,
                    )
                elif (
                    azure_web_search_contract == "chat_completions"
                    and self._is_chat_completions_web_search_unsupported_error(exc)
                ):
                    self._azure_chat_web_search_supported = False
                    self._save_runtime_web_search_observation(
                        status="unsupported",
                        reason="azure_chat_completions_web_search_runtime_rejected",
                    )
                    logger.warning(
                        "Azure model '%s' does not support chat.completions web search; "
                        "falling back to regular chat.completions stream.",
                        self.model_name,
                    )
                else:
                    raise
                if self._user_requested_web_search(context):
                    response = self._build_web_search_unavailable_response(exc)
                    self._buffered_text = (
                        [response.text_content] if response.text_content else []
                    )
                    self._buffered_tool_calls = []
                    self._buffered_usage = {"input": 0, "output": 0}
                    self._buffered_stop_reason = response.stop_reason
                    if response.text_content:
                        yield StreamChunk(text=response.text_content)
                    yield StreamChunk(
                        is_final=True,
                        usage={"input_tokens": 0, "output_tokens": 0},
                    )
                    return

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
            else ToolCallMode.PROMPT_JSON,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=cost,
            model=self.model_name,
            provider=self.provider_name,
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

    def _resolve_azure_web_search_contract(
        self,
        *,
        request_options: ProviderRequestOptions | None,
    ) -> str:
        if request_options is None or not request_options.web_search_enabled:
            return ""
        if self.provider_name != "azure":
            return ""

        if self._api_surface == "responses_api":
            if self._azure_responses_web_search_supported is False:
                return ""
            if not hasattr(self.client, "responses") or not hasattr(
                self.client.responses, "create"
            ):
                self._save_runtime_web_search_observation(
                    status="unsupported",
                    reason="azure_responses_api_unavailable_in_sdk_client",
                )
                logger.warning(
                    "Azure web_search requested but responses API is unavailable in SDK client."
                )
                return ""
            return "responses_api"

        if self._api_surface == "chat_completions":
            if self._azure_chat_web_search_supported is False:
                return ""
            if not (
                hasattr(self.client, "chat")
                and hasattr(self.client.chat, "completions")
                and hasattr(self.client.chat.completions, "create")
            ):
                self._save_runtime_web_search_observation(
                    status="unsupported",
                    reason="azure_chat_completions_unavailable_in_sdk_client",
                )
                return ""
            if not self._azure_chat_web_search_contract_available():
                self._save_runtime_web_search_observation(
                    status="unsupported",
                    reason="azure_chat_completions_web_search_contract_unavailable",
                )
                return ""
            return "chat_completions"

        self._save_runtime_web_search_observation(
            status="unsupported",
            reason=f"azure_api_surface_not_supported_for_web_search:{self._api_surface}",
        )
        return ""

    async def _generate_with_azure_web_search(
        self,
        *,
        context: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        tool = self._build_azure_web_search_tool()
        instructions, response_input = self._build_responses_input(context)

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "tools": [tool],
            "input": response_input if response_input else "",
        }
        if instructions:
            kwargs["instructions"] = instructions
        if max_tokens > 0:
            kwargs["max_output_tokens"] = max_tokens

        response = await self.client.responses.create(**kwargs)
        text = str(getattr(response, "output_text", "") or "")
        if not text:
            text = self._extract_responses_output_text(response)
        if text:
            text = self._coerce_to_notify_user_json(text)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = self.estimate_cost(input_tokens, output_tokens)

        return LLMResponse(
            text_content=text,
            tool_calls=[],
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider=self.provider_name,
            stop_reason=StopReason.END_TURN,
        )

    async def _generate_with_azure_chat_web_search(
        self,
        *,
        context: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        kwargs = self._build_azure_chat_web_search_payload(
            context=context,
            max_tokens=max_tokens,
        )
        response = await self.client.chat.completions.create(**kwargs)
        return self._normalize(response)

    def _azure_chat_web_search_contract_available(self) -> bool:
        capabilities = self._data_registry.get_model_capabilities(self.model_name)
        if capabilities is None or not capabilities.web_search.supported:
            return False
        chat_profile = self._azure_chat_web_search_profile()
        profile_tool_type = str(chat_profile.get("tool_type", "")).strip()
        if capabilities.web_search.tool_type or profile_tool_type:
            return True
        mutations = chat_profile.get("mutations")
        return bool(isinstance(mutations, list) and mutations)

    def _build_azure_chat_web_search_payload(
        self,
        *,
        context: List[Dict[str, Any]],
        max_tokens: int,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": context,
        }
        kwargs.update(self._max_tokens_kwargs(max_tokens))

        chat_profile = self._azure_chat_web_search_profile()
        capabilities = self._data_registry.get_model_capabilities(self.model_name)
        tool_type = ""
        if capabilities is not None and capabilities.web_search.tool_type:
            tool_type = str(capabilities.web_search.tool_type).strip()
        if not tool_type:
            tool_type = str(chat_profile.get("tool_type", "")).strip()
        if tool_type:
            kwargs["extra_body"] = {"tools": [{"type": tool_type}]}

        mutations = chat_profile.get("mutations")
        if isinstance(mutations, list):
            kwargs = self._apply_chat_web_search_mutations(kwargs, mutations)
        return kwargs

    def _build_azure_web_search_tool(self) -> Dict[str, Any]:
        tool_type = "web_search_preview"
        caps = self._data_registry.get_model_capabilities(self.model_name)
        if caps and caps.web_search and caps.web_search.tool_type:
            tool_type = str(caps.web_search.tool_type).strip() or tool_type

        defaults = self._data_registry.get_web_search_defaults()

        tool_type = str(tool_type).strip()
        if not tool_type:
            tool_type = "web_search_preview"

        tool: Dict[str, Any] = {"type": tool_type}
        context_size = str(defaults.get("search_context_size", "")).strip().lower()
        if context_size in {"low", "medium", "high"}:
            tool["search_context_size"] = context_size

        location = defaults.get("user_location")
        if isinstance(location, dict):
            normalized_location = {str(k): v for k, v in location.items()}
            if normalized_location:
                tool["user_location"] = normalized_location
        return tool

    def _build_responses_input(
        self,
        context: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, str]]]:
        instructions, messages = self._split_system_message(context)
        normalized: List[Dict[str, str]] = []
        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = str(msg.get("content", ""))

            if role not in {"user", "assistant"}:
                if role == "tool":
                    content = f"[Tool Result]\n{content}"
                role = "user"
            normalized.append({"role": role, "content": content})
        return instructions, normalized

    def _azure_chat_web_search_profile(self) -> Dict[str, Any]:
        profile = self._resolve_api_profile()
        web_search = profile.get("web_search")
        if not isinstance(web_search, dict):
            return {}
        chat_profile = web_search.get("chat_completions")
        if isinstance(chat_profile, dict):
            return chat_profile
        if "mutations" in web_search or "tool_type" in web_search:
            return web_search
        return {}

    def _resolve_api_profile(self) -> Dict[str, Any]:
        if self._api_profile:
            return deepcopy(self._api_profile)
        provider_profile = self._data_registry.get_provider_api_profile(self.provider_name)
        if isinstance(provider_profile, dict):
            return provider_profile
        return {}

    def _apply_chat_web_search_mutations(
        self,
        payload: Dict[str, Any],
        mutations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not mutations:
            return payload

        mutated = deepcopy(payload)
        for mutation in mutations:
            if not isinstance(mutation, dict):
                continue
            op = str(mutation.get("op", "")).strip().lower()
            value = mutation.get("value")
            if op == "append_model_suffix":
                if not isinstance(value, str):
                    continue
                suffix = value.strip()
                if not suffix:
                    continue
                model = str(mutated.get("model", "")).strip()
                if model and not model.endswith(suffix):
                    mutated["model"] = f"{model}{suffix}"
                continue
            if op == "merge_body":
                if not isinstance(value, dict):
                    continue
                extra_body = mutated.get("extra_body")
                if not isinstance(extra_body, dict):
                    extra_body = {}
                    mutated["extra_body"] = extra_body
                self._deep_merge(extra_body, value)
                continue
            logger.debug(
                "Unsupported azure chat web_search mutation op '%s' for provider '%s'",
                op,
                self.provider_name,
            )
        return mutated

    @staticmethod
    def _deep_merge(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
        for key, value in patch.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                OpenAIProvider._deep_merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    @staticmethod
    def _is_responses_api_unsupported_error(error: Exception) -> bool:
        def _to_lower_text(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.lower()
            try:
                return json.dumps(value, ensure_ascii=False).lower()
            except Exception:
                return str(value).lower()

        message = _to_lower_text(str(error))
        body_text = _to_lower_text(getattr(error, "body", None))
        combined = f"{message} {body_text}".strip()

        if "this model is not supported by responses api" in combined:
            return True
        if "responses api" in combined and "not supported" in combined:
            return True
        if "responses api" in combined and "invalid_request_error" in combined:
            return True
        return False

    @staticmethod
    def _is_chat_completions_web_search_unsupported_error(error: Exception) -> bool:
        def _to_lower_text(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.lower()
            try:
                return json.dumps(value, ensure_ascii=False).lower()
            except Exception:
                return str(value).lower()

        message = _to_lower_text(str(error))
        body_text = _to_lower_text(getattr(error, "body", None))
        combined = f"{message} {body_text}".strip()

        unsupported_signals = (
            "not supported",
            "unsupported",
            "not available",
            "unknown parameter",
            "unrecognized",
            "invalid_request_error",
            "invalid value",
        )
        web_search_markers = (
            "web_search",
            "web search",
            "web_search_preview",
            "search_preview",
            "tools",
            "extensions",
        )

        if any(marker in combined for marker in web_search_markers) and any(
            signal in combined for signal in unsupported_signals
        ):
            return True
        if "this model is not supported" in combined and (
            "chat completion" in combined or "chat.completions" in combined
        ):
            return True
        return False

    @staticmethod
    def _user_requested_web_search(context: List[Dict[str, Any]]) -> bool:
        for msg in reversed(context):
            if str(msg.get("role", "")).strip().lower() != "user":
                continue
            content = str(msg.get("content", "")).strip().lower()
            if not content:
                return False
            patterns = (
                r"\bweb[\s_-]?search\b",
                r"\bsearch (the )?web\b",
                r"\bsearch online\b",
                r"\blook up (online|on the web)\b",
            )
            return any(re.search(pattern, content) for pattern in patterns)
        return False

    def _build_web_search_unavailable_response(self, error: Exception) -> LLMResponse:
        error_message = str(error).strip()
        if not error_message:
            error_message = (
                "Provider-managed web search is unavailable for this deployment."
            )
        payload = {
            "title": "Web Search Unavailable",
            "thought": (
                "Provider-managed web search is unsupported by this Azure deployment."
            ),
            "decision": {
                "type": "notify_user",
                "message": (
                    "Web search request failed at provider level. "
                    f"System response: {error_message}"
                ),
            },
        }
        return LLMResponse(
            text_content=json.dumps(payload, ensure_ascii=False),
            tool_calls=[],
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            model=self.model_name,
            provider=self.provider_name,
            stop_reason=StopReason.END_TURN,
        )

    def _save_runtime_web_search_observation(self, *, status: str, reason: str) -> None:
        """Persist runtime web_search capability observations when mismatch occurs."""
        registry = self._data_registry
        if registry is None:
            return

        provider_name = str(self.provider_name).strip()
        model_name = str(self.model_name).strip()
        if not provider_name or not model_name:
            return

        deployment_id = self._build_deployment_id(
            provider_name=provider_name,
            model_name=model_name,
            base_url=str(self.base_url or ""),
        )
        try:
            registry.save_capability_observation(
                provider=provider_name,
                model=model_name,
                deployment_id=deployment_id,
                observation={
                    "web_search": {
                        "status": str(status).strip().lower() or "unknown",
                        "reason": str(reason).strip(),
                        "checked_at": datetime.now(timezone.utc),
                        "source": "runtime",
                    }
                },
            )
        except Exception:
            logger.debug(
                "Failed to persist runtime web_search capability observation",
                exc_info=True,
            )

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

    @staticmethod
    def _normalize_api_surface(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"responses_api", "responses"}:
            return "responses_api"
        return "chat_completions"

    @staticmethod
    def _extract_responses_output_text(response: Any) -> str:
        output = getattr(response, "output", None) or []
        parts: List[str] = []
        for item in output:
            if str(getattr(item, "type", "")).strip() != "message":
                continue
            content = getattr(item, "content", None) or []
            for block in content:
                block_type = str(getattr(block, "type", "")).strip()
                if block_type == "output_text":
                    text = str(getattr(block, "text", "") or "")
                    if text:
                        parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _coerce_to_notify_user_json(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
            if (
                isinstance(parsed, dict)
                and isinstance(parsed.get("decision"), dict)
                and isinstance(parsed["decision"].get("type"), str)
            ):
                return stripped
        except json.JSONDecodeError:
            pass

        payload = {
            "title": "Web Search Result",
            "thought": "Returning grounded search response.",
            "decision": {"type": "notify_user", "message": stripped},
        }
        return json.dumps(payload, ensure_ascii=False)
