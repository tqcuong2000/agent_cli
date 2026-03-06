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
from typing import Any, List, Optional, Set

from agent_cli.core.runtime.agents.parsers import AgentDecision, AgentResponse, ParsedAction
from agent_cli.core.infra.events.errors import SchemaValidationError
from agent_cli.core.infra.config.config_models import ProtocolMode
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.models import LLMResponse, ToolCallMode

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
        *,
        data_registry: DataRegistry,
        protocol_mode: ProtocolMode | str = ProtocolMode.JSON_ONLY,
        multi_action_enabled: bool = False,
    ) -> None:
        self._registered_tools: Set[str] = set(registered_tools)
        if isinstance(protocol_mode, ProtocolMode):
            self._protocol_mode = protocol_mode
        else:
            self._protocol_mode = ProtocolMode(str(protocol_mode).strip().lower())
        self._multi_action_enabled = bool(multi_action_enabled)
        schema_defaults = data_registry.get_schema_defaults()
        title_defaults = schema_defaults.get("title", {})
        self._title_max_words = int(title_defaults.get("max_words", 15))

    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response."""
        parsed: AgentResponse | None = None
        if response.tool_mode == ToolCallMode.NATIVE:
            native_response = self._parse_native_fc(response)
            if native_response is not None:
                parsed = native_response

        if parsed is None:
            parsed = self._parse_json_response(response.text_content, strict=True)
        if parsed is None:
            raise self._empty_response_error(response.text_content)

        parsed = self._normalize_action_response(parsed)
        parsed = self._reconstruct_native_fc_audit_trail(response, parsed)
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

    @property
    def multi_action_enabled(self) -> bool:
        return self._multi_action_enabled

    def _parse_native_fc(self, response: LLMResponse) -> Optional[AgentResponse]:
        """Parse structured tool calls from native function-calling."""
        if not response.tool_calls:
            return None

        title, _ = self._extract_json_reasoning(response.text_content)
        thought = self.extract_thinking(response.text_content)

        if len(response.tool_calls) > 1:
            if not self._multi_action_enabled:
                raise SchemaValidationError(
                    "Multiple native tool calls found. You must call exactly ONE tool per response and wait for the result.",
                    raw_response=response.text_content,
                )

            parsed_actions: List[ParsedAction] = []
            for idx, tc in enumerate(response.tool_calls):
                if tc.tool_name not in self._registered_tools:
                    raise SchemaValidationError(
                        f"Unknown tool '{tc.tool_name}'. "
                        f"Available tools: {', '.join(sorted(self._registered_tools))}",
                        raw_response=response.text_content,
                    )

                native_call_id = tc.native_call_id.strip()
                action_id = native_call_id or f"act_{idx}"
                parsed_actions.append(
                    ParsedAction(
                        tool_name=tc.tool_name,
                        arguments=tc.arguments,
                        native_call_id=native_call_id,
                        action_id=action_id,
                    )
                )

            return AgentResponse(
                decision=AgentDecision.EXECUTE_ACTIONS,
                title=title,
                thought=thought,
                actions=parsed_actions,
                final_answer=None,
            )

        tc = response.tool_calls[0]
        if tc.tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tc.tool_name}'. "
                f"Available tools: {', '.join(sorted(self._registered_tools))}",
                raw_response=response.text_content,
            )

        return AgentResponse(
            decision=AgentDecision.EXECUTE_ACTION,
            title=title,
            thought=thought,
            action=ParsedAction(
                tool_name=tc.tool_name,
                arguments=tc.arguments,
                native_call_id=tc.native_call_id,
            ),
            final_answer=None,
        )

    def _normalize_action_response(self, response: AgentResponse) -> AgentResponse:
        """Normalize execute_action vs execute_actions representation."""
        if (
            response.decision == AgentDecision.EXECUTE_ACTIONS
            and response.actions is not None
            and len(response.actions) == 1
        ):
            return AgentResponse(
                decision=AgentDecision.EXECUTE_ACTION,
                title=response.title,
                thought=response.thought,
                action=response.actions[0],
                actions=None,
                final_answer=response.final_answer,
                intent=response.intent,
            )
        return response

    def _reconstruct_native_fc_audit_trail(
        self,
        raw_response: LLMResponse,
        parsed: AgentResponse,
    ) -> AgentResponse:
        """Backfill missing JSON audit fields when native FC slips occur."""
        if self._protocol_mode != ProtocolMode.JSON_ONLY:
            return parsed
        if raw_response.tool_mode != ToolCallMode.NATIVE:
            return parsed
        if parsed.decision not in {
            AgentDecision.EXECUTE_ACTION,
            AgentDecision.EXECUTE_ACTIONS,
        }:
            return parsed

        if parsed.title and parsed.thought:
            return parsed

        tool_names: List[str] = []
        if parsed.action is not None:
            tool_names.append(parsed.action.tool_name)
        elif parsed.actions is not None:
            tool_names.extend(action.tool_name for action in parsed.actions)

        generated_title = parsed.title.strip() or f"Call {', '.join(tool_names)}"
        if not generated_title.strip():
            generated_title = "Call tool"

        generated_thought = parsed.thought.strip()
        if not generated_thought:
            generated_thought = self._format_reasoning(
                title=generated_title,
                thoughts="[Auto-reconstructed from native function call]",
            )

        logger.warning(
            "Native FC format slip detected; reconstructed audit trail fields: %s",
            generated_title,
        )
        return AgentResponse(
            decision=parsed.decision,
            title=generated_title,
            thought=generated_thought,
            action=parsed.action,
            actions=parsed.actions,
            final_answer=parsed.final_answer,
            intent=parsed.intent,
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
        title = self._normalize_title(title=title, thought=thought_text)
        thought = self._format_reasoning(title=title, thoughts=thought_text)

        if decision_type == AgentDecision.REFLECT.value:
            return AgentResponse(
                decision=AgentDecision.REFLECT,
                title=title,
                thought=thought,
                action=None,
                final_answer=None,
                intent="",
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
                intent="",
            )

        if decision_type == AgentDecision.EXECUTE_ACTIONS.value:
            if not self._multi_action_enabled:
                raise SchemaValidationError(
                    "decision.type=execute_actions is disabled for this agent/runtime.",
                    raw_response=text,
                )

            raw_actions = decision.get("actions")
            if not isinstance(raw_actions, list) or len(raw_actions) == 0:
                raise SchemaValidationError(
                    "decision.actions must be a non-empty list for execute_actions.",
                    raw_response=text,
                )

            parsed_actions: List[ParsedAction] = []
            for idx, raw_action in enumerate(raw_actions):
                if not isinstance(raw_action, dict):
                    raise SchemaValidationError(
                        f"Action[{idx}] must be an object.",
                        raw_response=text,
                    )

                tool_name = str(raw_action.get("tool", "")).strip()
                if not tool_name:
                    raise SchemaValidationError(
                        f"Action at index {idx} missing 'tool' field.",
                        raw_response=text,
                    )
                if tool_name not in self._registered_tools:
                    raise SchemaValidationError(
                        f"Unknown tool '{tool_name}' in action[{idx}].",
                        raw_response=text,
                    )

                arguments = raw_action.get("args", {})
                if arguments is None:
                    arguments = {}
                if not isinstance(arguments, dict):
                    raise SchemaValidationError(
                        f"Action[{idx}].args must be an object.",
                        raw_response=text,
                    )

                parsed_actions.append(
                    ParsedAction(
                        tool_name=tool_name,
                        arguments=arguments,
                        action_id=f"act_{idx}",
                    )
                )

            return AgentResponse(
                decision=AgentDecision.EXECUTE_ACTIONS,
                title=title,
                thought=thought,
                actions=parsed_actions,
                final_answer=None,
                intent="",
            )

        if decision_type in {
            AgentDecision.NOTIFY_USER.value,
            AgentDecision.YIELD.value,
        }:
            intent = ""
            if decision_type == AgentDecision.NOTIFY_USER.value:
                intent = str(decision.get("intent", "")).strip()

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
                intent=intent,
            )

        raise SchemaValidationError(
            "Unknown decision.type. Allowed values: "
            "reflect, execute_action, execute_actions, notify_user, yield.",
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

    def _normalize_title(self, *, title: str, thought: str) -> str:
        """Normalize title, auto-generating one when missing."""
        words = [word for word in title.split() if word]
        if words:
            return " ".join(words[: self._title_max_words]).strip()

        thought_words = [word for word in thought.split() if word]
        if thought_words:
            generated = " ".join(thought_words[:5])
            if len(thought_words) > 5:
                generated = f"{generated}..."
            logger.debug("Title auto-generated: %s", generated)
            return generated

        logger.debug("Title auto-generated: Untitled")
        return "Untitled"

    @staticmethod
    def _empty_response_error(raw_response: str) -> SchemaValidationError:
        return SchemaValidationError(
            "Response contains no reasoning, no tool call, and no final answer. "
            "Return one valid JSON decision object.",
            raw_response=raw_response,
        )
