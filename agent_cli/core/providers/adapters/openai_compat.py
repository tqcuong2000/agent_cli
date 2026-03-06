"""
OpenAI-Compatible Provider — adapter for Ollama, LM Studio, vLLM, etc.

Uses the ``openai`` SDK pointed at a custom ``base_url``.  Defaults to
prompt-mode JSON tool calling (no native FC) unless ``native_tools=True``.

Registered via TOML::

    [providers.local_ollama]
    adapter_type = "openai_compatible"
    base_url = "http://localhost:11434/v1"
    models = ["llama-3-8b", "codestral"]
"""

from __future__ import annotations

import importlib
import json
import logging
from copy import deepcopy
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.core.infra.config.config_models import EffortLevel
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.core.providers.base.json_formatter import JSONToolFormatter
from agent_cli.core.providers.base.models import (
    LLMResponse,
    ProviderRequestOptions,
    StreamChunk,
    ToolCall,
    ToolCallMode,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseLLMProvider):
    """Adapter for any OpenAI-compatible API endpoint.

    Works with Ollama, LM Studio, vLLM, LocalAI, and any service
    that exposes the ``/v1/chat/completions`` endpoint.

    By default uses prompt-mode JSON tool calling. Set ``native_tools=True``
    if the endpoint supports OpenAI-style function calling.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: str = "http://localhost:11434/v1",
        native_tools: bool = False,
        api_surface: str = "chat_completions",
        api_profile: Optional[Dict[str, Any]] = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._native_tools = native_tools
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

        self.client = async_openai_cls(
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

    @property
    def api_surface(self) -> str:
        return self._api_surface

    @property
    def supports_web_search(self) -> bool:
        profile = self._get_web_search_profile()
        return bool(profile)

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
        # For prompt-mode JSON: inject tools into the system prompt.
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
            from agent_cli.core.providers.adapters.openai_provider import OpenAIToolFormatter

            kwargs["tools"] = OpenAIToolFormatter().format_for_native_fc(tools)
        if request_options is not None and request_options.web_search_enabled:
            kwargs = self._apply_web_search_mutations(kwargs)

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract usage if available (some local servers don't report it)
        input_tokens = (
            getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
        )
        output_tokens = (
            getattr(response.usage, "completion_tokens", 0) if response.usage else 0
        )

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
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.PROMPT_JSON,
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
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        _ = effort
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
        if request_options is not None and request_options.web_search_enabled:
            kwargs = self._apply_web_search_mutations(kwargs)

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
            tool_calls=[],  # Prompt-mode parsing is done by the Schema Validator
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=0.0,
            model=self.model_name,
            provider="openai_compatible",
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return JSONToolFormatter()

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0  # Local models are typically free

    def _apply_web_search_mutations(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        profile = self._get_web_search_profile()
        if not profile:
            return payload

        mutations = profile.get("mutations")
        if not isinstance(mutations, list) or not mutations:
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
                "Unsupported api_profile web_search mutation op '%s' for provider '%s'",
                op,
                self.provider_name,
            )
        return mutated

    def _get_web_search_profile(self) -> Dict[str, Any]:
        profile = self._resolve_api_profile()
        web_search = profile.get("web_search")
        if isinstance(web_search, dict):
            return web_search
        return {}

    def _resolve_api_profile(self) -> Dict[str, Any]:
        if self._api_profile:
            return deepcopy(self._api_profile)
        provider_profile = self._data_registry.get_provider_api_profile(self.provider_name)
        if isinstance(provider_profile, dict):
            return provider_profile
        return {}

    @staticmethod
    def _deep_merge(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
        for key, value in patch.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                OpenAICompatibleProvider._deep_merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    @staticmethod
    def _normalize_api_surface(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"responses_api", "responses"}:
            return "responses_api"
        return "chat_completions"
