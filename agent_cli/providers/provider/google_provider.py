"""
Google Provider — adapter for Gemini models via the ``google-genai`` SDK.

Uses the new unified ``google.genai`` package (the ``google-generativeai``
package was deprecated in November 2025).  Supports native function
declarations and streaming.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from agent_cli.core.models.config_models import EffortLevel, normalize_effort
from agent_cli.core.registry import DataRegistry
from agent_cli.providers.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.providers.json_formatter import JSONToolFormatter
from agent_cli.providers.models import (
    LLMResponse,
    ProviderRequestOptions,
    StreamChunk,
    ToolCall,
    ToolCallMode,
)

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
        return JSONToolFormatter().format_for_prompt_injection(tools)


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
        self._buffered_web_search_mode: bool = False

    @property
    def provider_name(self) -> str:
        return "google"

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
        """Google supports up to HIGH; map MAX down to HIGH."""
        requested = normalize_effort(effort)
        if requested == EffortLevel.MAX:
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
        types = _google_types_module()

        system_msg, gemini_history = self._convert_messages(context)

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system_msg:
            config.system_instruction = system_msg
        request_tools: List[Any] = []
        use_web_search = self._web_search_enabled(request_options)
        if use_web_search:
            # Gemini rejects requests that combine google_search grounding
            # and function declarations in the same request.
            web_search_tool = self._build_web_search_tool(types)
            if web_search_tool is not None:
                request_tools.append(web_search_tool)
        elif tools:
            request_tools.extend(self._tool_formatter.format_for_native_fc(tools))
        if request_tools:
            config.tools = request_tools
        self._apply_effort_to_config(config=config, effort=effort, types=types)

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=gemini_history,
            config=config,
        )

        return self._normalize(response, coerce_notify_user_json=use_web_search)

    def _normalize(
        self,
        response: Any,
        *,
        coerce_notify_user_json: bool = False,
    ) -> LLMResponse:
        text = ""
        tool_calls = []

        candidates = getattr(response, "candidates", None) or []
        first_candidate = candidates[0] if candidates else None
        content = getattr(first_candidate, "content", None) if first_candidate else None
        parts = getattr(content, "parts", None) or []

        for part in parts:
            if part.text:
                text += part.text
            elif part.function_call:
                fc = part.function_call
                if coerce_notify_user_json and self._is_provider_managed_tool_call(
                    fc.name
                ):
                    continue
                tool_calls.append(
                    ToolCall(
                        tool_name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                        mode=ToolCallMode.NATIVE,
                    )
                )

        if not text:
            # Some SDK responses expose plain text even when parts are missing.
            text = str(getattr(response, "text", "") or "")
        if coerce_notify_user_json and text:
            text = self._coerce_to_notify_user_json(text)

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
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
        effort: str | EffortLevel | None = None,
        request_options: ProviderRequestOptions | None = None,
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
        request_tools: List[Any] = []
        use_web_search = self._web_search_enabled(request_options)
        self._buffered_web_search_mode = use_web_search
        if use_web_search:
            # Gemini rejects requests that combine google_search grounding
            # and function declarations in the same request.
            web_search_tool = self._build_web_search_tool(types)
            if web_search_tool is not None:
                request_tools.append(web_search_tool)
        elif tools:
            request_tools.extend(self._tool_formatter.format_for_native_fc(tools))
        if request_tools:
            config.tools = request_tools
        self._apply_effort_to_config(config=config, effort=effort, types=types)

        async for chunk in self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=gemini_history,
            config=config,
        ):
            candidates = getattr(chunk, "candidates", None) or []
            first_candidate = candidates[0] if candidates else None
            content = (
                getattr(first_candidate, "content", None) if first_candidate else None
            )
            parts = getattr(content, "parts", None) or []

            for part in parts:
                if part.text:
                    self._buffered_text.append(part.text)
                    yield StreamChunk(text=part.text)
                elif part.function_call:
                    fc = part.function_call
                    if use_web_search and self._is_provider_managed_tool_call(fc.name):
                        continue
                    self._buffered_tool_calls.append(
                        ToolCall(
                            tool_name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                            mode=ToolCallMode.NATIVE,
                        )
                    )

            if getattr(chunk, "usage_metadata", None):
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
        if self._buffered_web_search_mode and text:
            text = self._coerce_to_notify_user_json(text)
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

    def _web_search_enabled(
        self,
        request_options: ProviderRequestOptions | None,
    ) -> bool:
        if request_options is None or not request_options.web_search_enabled:
            return False
        registry = self._data_registry or DataRegistry()
        defaults = registry.get_web_search_provider_defaults("google")
        return bool(defaults.get("enabled", True))

    @staticmethod
    def _build_web_search_tool(types: Any) -> Any | None:
        """Build a Google Search tool declaration, supporting SDK variants."""
        try:
            if hasattr(types, "GoogleSearch"):
                return types.Tool(google_search=types.GoogleSearch())
            if hasattr(types, "GoogleSearchRetrieval"):
                return types.Tool(google_search_retrieval=types.GoogleSearchRetrieval())
        except Exception:
            return None
        return None

    @staticmethod
    def _coerce_to_notify_user_json(text: str) -> str:
        """Wrap plain text into a valid prompt JSON final-answer envelope."""
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

    @staticmethod
    def _is_provider_managed_tool_call(tool_name: Any) -> bool:
        normalized = str(tool_name or "").strip().lower()
        return normalized in {"google_search", "google_search_retrieval", "web_search"}

    @staticmethod
    def _resolve_google_thinking_level(
        effort: str | EffortLevel | None,
        types: Any,
    ) -> Any | None:
        """Map canonical effort values to google.genai thinking levels."""
        try:
            normalized = normalize_effort(effort)
        except Exception:
            return None

        if normalized == EffortLevel.AUTO:
            return None

        mapping = {
            EffortLevel.MINIMAL: types.ThinkingLevel.MINIMAL,
            EffortLevel.LOW: types.ThinkingLevel.LOW,
            EffortLevel.MEDIUM: types.ThinkingLevel.MEDIUM,
            EffortLevel.HIGH: types.ThinkingLevel.HIGH,
            # Google SDK currently tops out at HIGH; map MAX to highest available.
            EffortLevel.MAX: types.ThinkingLevel.HIGH,
        }
        return mapping.get(normalized)

    def _apply_effort_to_config(
        self,
        *,
        config: Any,
        effort: str | EffortLevel | None,
        types: Any,
    ) -> None:
        """Apply model effort to request config when supported by Google SDK."""
        level = self._resolve_google_thinking_level(effort, types)
        if level is None:
            return

        config.thinking_config = types.ThinkingConfig(thinking_level=level)

    @staticmethod
    def _convert_messages(
        context: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Convert OpenAI-style messages to Gemini format.

        Returns:
            (system_instruction, gemini_history)
        """
        system_parts: List[str] = []
        converted: List[Dict[str, Any]] = []
        for msg in context:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))

            if role == "system":
                if content.strip():
                    system_parts.append(content.strip())
            elif role == "assistant":
                converted.append({"role": "model", "parts": [{"text": content}]})
            else:
                converted.append({"role": "user", "parts": [{"text": content}]})

        system = "\n\n".join(system_parts).strip()
        return system, converted


def _google_types_module() -> Any:
    genai_mod = importlib.import_module("google.genai")
    return getattr(genai_mod, "types")
