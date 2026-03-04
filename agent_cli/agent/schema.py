"""
Schema Validator - JSON protocol response validation.

Validates and normalizes every ``LLMResponse`` into an ``AgentResponse``
before the Agent loop processes it.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Set

from agent_cli.agent.parsers import AgentDecision, AgentResponse, ParsedAction
from agent_cli.core.error_handler.errors import SchemaValidationError
from agent_cli.core.models.config_models import ProtocolMode
from agent_cli.data import DataRegistry
from agent_cli.providers.models import LLMResponse, ToolCallMode

logger = logging.getLogger(__name__)


class BaseSchemaValidator(ABC):
    """Validates and normalizes LLM responses into ``AgentResponse``."""

    @abstractmethod
    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response."""

    @abstractmethod
    def extract_thinking(self, text: str) -> str:
        """Extract normalized reasoning text for TUI streaming."""


class SchemaValidator(BaseSchemaValidator):
    """Production implementation for JSON protocol parsing."""

    def __init__(
        self,
        registered_tools: list[str],
        protocol_mode: ProtocolMode | str = ProtocolMode.JSON_ONLY,
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self._registered_tools: Set[str] = set(registered_tools)
        if isinstance(protocol_mode, ProtocolMode):
            self._protocol_mode = protocol_mode
        else:
            self._protocol_mode = ProtocolMode(str(protocol_mode).strip().lower())
        schema_defaults = (data_registry or DataRegistry()).get_schema_defaults()
        title_defaults = schema_defaults.get("title", {})
        self._title_max_words = int(title_defaults.get("max_words", 15))

    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response."""
        if response.tool_mode == ToolCallMode.NATIVE:
            action = self._parse_native_fc(response)
            if action is not None:
                title, _ = self._extract_json_reasoning(response.text_content)
                return AgentResponse(
                    decision=AgentDecision.EXECUTE_ACTION,
                    title=title,
                    thought=self.extract_thinking(response.text_content),
                    action=action,
                    final_answer=None,
                )

        parsed = self._parse_json_response(response.text_content, strict=True)
        if parsed is None:
            raise self._empty_response_error(response.text_content)
        return parsed

    def extract_thinking(self, text: str) -> str:
        """Extract reasoning from JSON payload."""
        title, thought = self._extract_json_reasoning(text)
        if not thought:
            return ""
        return self._format_reasoning(title=title, thoughts=thought)

    @property
    def protocol_mode(self) -> ProtocolMode:
        """Active parser protocol mode."""
        return self._protocol_mode

    def _parse_native_fc(self, response: LLMResponse) -> Optional[ParsedAction]:
        """Parse structured tool calls from native function-calling."""
        if not response.tool_calls:
            return None

        if len(response.tool_calls) > 1:
            raise SchemaValidationError(
                "Multiple native tool calls found. You must call exactly ONE tool per response and wait for the result.",
                raw_response=response.text_content,
            )

        tc = response.tool_calls[0]
        if tc.tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tc.tool_name}'. "
                f"Available tools: {', '.join(sorted(self._registered_tools))}",
                raw_response=response.text_content,
            )

        return ParsedAction(
            tool_name=tc.tool_name,
            arguments=tc.arguments,
            native_call_id=tc.native_call_id,
        )

    def _parse_json_response(
        self,
        text: str,
        *,
        strict: bool,
    ) -> Optional[AgentResponse]:
        """Parse prompt-mode JSON decision payload."""
        data = self._extract_json_object(text)
        if data is None:
            if strict:
                raise SchemaValidationError(
                    "Response is not valid JSON. Return exactly one JSON object "
                    "with fields: title, thought, decision{type,...}.",
                    raw_response=text,
                )
            return None

        decision = data.get("decision")
        if not isinstance(decision, dict):
            raise SchemaValidationError(
                "JSON response must contain a 'decision' object.",
                raw_response=text,
            )

        raw_type = decision.get("type")
        if not isinstance(raw_type, str) or not raw_type.strip():
            raise SchemaValidationError(
                "decision.type is required and must be a non-empty string.",
                raw_response=text,
            )
        decision_type = raw_type.strip().lower()

        title = str(data.get("title", "")).strip()
        thought_text = str(data.get("thought", "")).strip()
        thought = self._format_reasoning(title=title, thoughts=thought_text)

        if decision_type == AgentDecision.REFLECT.value:
            return AgentResponse(
                decision=AgentDecision.REFLECT,
                title=title,
                thought=thought,
                action=None,
                final_answer=None,
            )

        if decision_type == AgentDecision.EXECUTE_ACTION.value:
            tool_name = decision.get("tool")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise SchemaValidationError(
                    "decision.tool is required for execute_action.",
                    raw_response=text,
                )
            tool_name = tool_name.strip()
            if tool_name not in self._registered_tools:
                raise SchemaValidationError(
                    f"Unknown tool '{tool_name}'. "
                    f"Available tools: {', '.join(sorted(self._registered_tools))}",
                    raw_response=text,
                )

            arguments = decision.get("args", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise SchemaValidationError(
                    "decision.args must be an object.",
                    raw_response=text,
                )

            return AgentResponse(
                decision=AgentDecision.EXECUTE_ACTION,
                title=title,
                thought=thought,
                action=ParsedAction(tool_name=tool_name, arguments=arguments),
                final_answer=None,
            )

        if decision_type in {
            AgentDecision.NOTIFY_USER.value,
            AgentDecision.YIELD.value,
        }:
            message = decision.get("message")
            if not isinstance(message, str) or not message.strip():
                if decision_type == AgentDecision.NOTIFY_USER.value:
                    message = str(data.get("final_answer", "")).strip()
                else:
                    message = str(data.get("yield", "")).strip()

            if not isinstance(message, str) or not message.strip():
                raise SchemaValidationError(
                    "decision.message is required for notify_user/yield.",
                    raw_response=text,
                )

            return AgentResponse(
                decision=(
                    AgentDecision.NOTIFY_USER
                    if decision_type == AgentDecision.NOTIFY_USER.value
                    else AgentDecision.YIELD
                ),
                title=title,
                thought=thought,
                action=None,
                final_answer=message.strip(),
            )

        raise SchemaValidationError(
            "Unknown decision.type. Allowed values: "
            "reflect, execute_action, notify_user, yield.",
            raw_response=text,
        )

    def _extract_json_reasoning(self, text: str) -> tuple[str, str]:
        """Extract ``title`` and ``thought`` from JSON response payload."""
        data = self._extract_json_object(text)
        if not data:
            return "", ""
        title = str(data.get("title", "")).strip()
        thought = str(data.get("thought", "")).strip()
        return title, thought

    def _extract_json_object(self, text: str) -> Optional[dict[str, Any]]:
        """Extract and parse the first JSON object from raw text."""
        stripped = text.strip()
        if not stripped:
            return None

        candidates: list[str] = [stripped]
        candidates.extend(self._repair_json_candidates(stripped))

        code_fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if code_fence:
            candidates.append(code_fence.group(1).strip())

        balanced = self._extract_balanced_json_object(stripped)
        if balanced:
            candidates.append(balanced)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    def _repair_json_candidates(self, text: str) -> list[str]:
        """Generate best-effort repaired JSON candidates for common model artifacts."""
        repaired: list[str] = []

        # Strip provider/tool sentinel artifacts sometimes appended after JSON.
        stripped_markers = re.sub(r"\s*<\|[^|>]+?\|>\s*", " ", text).strip()
        if stripped_markers and stripped_markers != text:
            repaired.append(stripped_markers)

        # Build candidates from the first object-looking slice onward.
        start = stripped_markers.find("{")
        if start >= 0:
            core = stripped_markers[start:].strip()
            repaired.append(core)

            # Common malformed case: one or more missing trailing "}".
            closed = self._close_unbalanced_braces(core)
            if closed != core:
                repaired.append(closed)

        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for candidate in repaired:
            if candidate and candidate not in seen:
                seen.add(candidate)
                unique.append(candidate)
        return unique

    @staticmethod
    def _close_unbalanced_braces(text: str) -> str:
        """Append missing closing braces if text appears to be truncated JSON."""
        depth = 0
        in_str = False
        escape_next = False
        for ch in text:
            if in_str:
                if escape_next:
                    escape_next = False
                elif ch == "\\":
                    escape_next = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(depth - 1, 0)

        if depth <= 0:
            return text
        return text + ("}" * depth)

    @staticmethod
    def _extract_balanced_json_object(text: str) -> Optional[str]:
        """Return the first balanced JSON object substring in text."""
        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        in_str = False
        escape_next = False

        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escape_next:
                    escape_next = False
                elif ch == "\\":
                    escape_next = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    def _format_reasoning(self, *, title: str, thoughts: str) -> str:
        """Return normalized reasoning as plain text for UI rendering."""
        normalized_thoughts = thoughts.strip()
        if not normalized_thoughts:
            return ""

        title_words = [w for w in title.split() if w]
        final_title = " ".join(title_words[: self._title_max_words]).strip()
        if not final_title:
            final_title = "Untitled Action"

        return f"Title: {final_title}\n{normalized_thoughts}"

    @staticmethod
    def _empty_response_error(raw_response: str) -> SchemaValidationError:
        return SchemaValidationError(
            "Response contains no reasoning, no tool call, and no final answer. "
            "Return one valid JSON decision object.",
            raw_response=raw_response,
        )
