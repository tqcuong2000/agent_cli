"""
Schema Validator — dual-mode LLM response validation.

Validates and normalizes every ``LLMResponse`` into an ``AgentResponse``
before the Agent loop processes it.  Supports two modes:

* **Native FC** (``ToolCallMode.NATIVE``) — structured tool calls
  already parsed by the provider API.  Validation is a safety check.
* **XML Prompting** (``ToolCallMode.XML``) — parse ``<action>`` tags
  and XML ``<args>`` payloads from raw text, handle missing tags.

Both modes share ``<thinking>`` extraction and ``<final_answer>``
detection logic.

See ``02_schema_verification.md`` for the full specification.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Any, Optional, Set

from agent_cli.agent.parsers import AgentDecision, AgentResponse, ParsedAction
from agent_cli.core.error_handler.errors import SchemaValidationError
from agent_cli.data import DataRegistry
from agent_cli.providers.models import LLMResponse, ToolCallMode

logger = logging.getLogger(__name__)

# ── Precompiled regex patterns ───────────────────────────────────────

_THINKING_PATTERN = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", re.DOTALL)
_TOOL_PATTERN = re.compile(r"<tool>(.*?)</tool>", re.DOTALL)
_ARGS_PATTERN = re.compile(r"<args>(.*?)</args>", re.DOTALL)
_FINAL_ANSWER_PATTERN = re.compile(r"<final_answer>(.*?)</final_answer>", re.DOTALL)
_YIELD_PATTERN = re.compile(r"<yield>(.*?)</yield>", re.DOTALL)


# ══════════════════════════════════════════════════════════════════════
# Abstract Interface
# ══════════════════════════════════════════════════════════════════════


class BaseSchemaValidator(ABC):
    """Validates and normalizes LLM responses into ``AgentResponse``.

    Supports dual mode: native function calling and XML prompting.
    """

    @abstractmethod
    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response.

        Automatically selects the correct parsing mode based on
        ``response.tool_mode``.

        Returns:
            An ``AgentResponse`` with thought, action, and/or final_answer.

        Raises:
            SchemaValidationError: If the response is malformed and
            cannot be coerced.  The agent loop catches this and feeds
            it back to the LLM as a correction prompt.
        """

    @abstractmethod
    def extract_thinking(self, text: str) -> str:
        """Extract content from ``<thinking>`` tags.

        Used by both modes (shared parsing logic).  Returns empty
        string if no thinking tags are found.
        """


# ══════════════════════════════════════════════════════════════════════
# Concrete Implementation
# ══════════════════════════════════════════════════════════════════════


class SchemaValidator(BaseSchemaValidator):
    """Production implementation with dual-mode support.

    Args:
        registered_tools: Set of known tool names for validation.
                          Obtained from ``ToolRegistry.get_all_names()``.
    """

    def __init__(
        self,
        registered_tools: list[str],
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self._registered_tools: Set[str] = set(registered_tools)
        schema_defaults = (data_registry or DataRegistry()).get_schema_defaults()
        title_defaults = schema_defaults.get("title", {})
        self._title_min_words = int(title_defaults.get("min_words", 2))
        self._title_max_words = int(title_defaults.get("max_words", 15))

    # ── Public API ───────────────────────────────────────────────

    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response (mode-aware).

        Determines the ``AgentDecision`` from the payload:
        - Has action       → EXECUTE_ACTION
        - Has <final_answer> → NOTIFY_USER
        - Has <yield>       → YIELD
        - Has thinking only → REFLECT
        - Has nothing       → SchemaValidationError
        """

        # ── Step 1: Extract thinking (same for both modes) ───────
        thinking = self.extract_thinking(response.text_content)

        # ── Step 2: Mode-specific action parsing ─────────────────
        if response.tool_mode == ToolCallMode.NATIVE:
            action = self._parse_native_fc(response)
            if action is None:
                # LLM hallucinated XML instead of using native function calling API
                action = self._parse_xml_prompting(response.text_content)
        else:
            action = self._parse_xml_prompting(response.text_content)

        # ── Step 3: Check for final answer or yield ──────────────
        final_answer: Optional[str] = None
        yield_reason: Optional[str] = None
        if action is None:
            final_answer = self._extract_final_answer(response.text_content)
            if final_answer is None:
                yield_reason = self._extract_yield(response.text_content)

        # ── Step 4: Determine decision ───────────────────────────
        if action is not None:
            decision = AgentDecision.EXECUTE_ACTION
        elif final_answer is not None:
            decision = AgentDecision.NOTIFY_USER
        elif yield_reason is not None:
            decision = AgentDecision.YIELD
            final_answer = yield_reason  # Carry yield reason in final_answer
        elif thinking:
            decision = AgentDecision.REFLECT
        else:
            raise SchemaValidationError(
                "Response contains no reasoning, no tool call, and no final answer. "
                "You must provide at least a <thinking> block, an <action>, "
                "a <final_answer>, or a <yield>.",
                raw_response=response.text_content,
            )

        # ── Step 5: Validate no leakage of text outside tags ─────
        self._check_text_leakage(response.text_content)

        return AgentResponse(
            decision=decision,
            thought=thinking,
            action=action,
            final_answer=final_answer,
        )

    def extract_thinking(self, text: str) -> str:
        """Extract and validate ``<title>`` + ``<thinking>`` payload.

        If multiple ``<thinking>`` blocks exist, they are concatenated
        with newlines (some models split reasoning across blocks).
        Returns normalized XML payload.
        If omitted or malformed, returns empty string gracefully.
        """
        matches = _THINKING_PATTERN.findall(text)
        if not matches:
            return ""

        thoughts = "\n".join(m.strip() for m in matches if m.strip()).strip()
        if not thoughts:
            return ""

        title_match = _TITLE_PATTERN.search(text)
        if not title_match:
            return f"<title>Untitled Action</title>\n<thinking>{thoughts}</thinking>"

        raw_title = title_match.group(1).strip()
        title_words = [w for w in raw_title.split() if w]

        # We no longer strictly crash on title length, just clamp it.
        final_title = " ".join(title_words[: self._title_max_words])
        if not final_title:
            final_title = "Untitled Action"

        return f"<title>{final_title}</title>\n<thinking>{thoughts}</thinking>"

    # ── Native FC Parsing (Trivial — already structured) ─────────

    def _parse_native_fc(self, response: LLMResponse) -> Optional[ParsedAction]:
        """Parse structured tool calls from native function calling.

        The API already enforced the schema, so this is mostly a
        safety check that the tool name exists in our registry.
        """
        if not response.tool_calls:
            return None

        # Ensure exactly one tool call is used
        if len(response.tool_calls) > 1:
            raise SchemaValidationError(
                "Multiple native tool calls found. You must call exactly ONE tool per response and wait for the result.",
                raw_response=response.text_content,
            )

        tc = response.tool_calls[0]

        # Validate tool name exists in our registry
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

    # ── XML Prompting Parsing ─────────────────────────────────────

    def _parse_xml_prompting(self, text: str) -> Optional[ParsedAction]:
        """Parse ``<action>`` XML tags from raw text output.

        Expects XML child tags inside ``<args>``.
        """
        # Look for <action>...</action> block
        action_matches = _ACTION_PATTERN.findall(text)
        if not action_matches:
            return None

        if len(action_matches) > 1:
            raise SchemaValidationError(
                "Multiple <action> blocks found. You must call exactly ONE tool per response and wait for the result.",
                raw_response=text,
            )

        action_block = action_matches[0]

        # ── Extract <tool> name ──────────────────────────────────
        tool_match = _TOOL_PATTERN.search(action_block)
        if not tool_match:
            raise SchemaValidationError(
                "Found <action> block but missing <tool> tag. "
                "Expected format: "
                "<action><tool>name</tool><args><arg>value</arg></args></action>",
                raw_response=text,
            )
        tool_name = tool_match.group(1).strip()

        # Validate tool name
        if tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tool_name}'. "
                f"Available tools: {', '.join(sorted(self._registered_tools))}",
                raw_response=text,
            )

        # ── Extract <args> XML ───────────────────────────────────
        args_match = _ARGS_PATTERN.search(action_block)
        if not args_match:
            raise SchemaValidationError(
                f"Tool '{tool_name}' is missing <args> block.",
                raw_response=text,
            )

        raw_args = args_match.group(1).strip()
        arguments = self._parse_xml_args(raw_args, tool_name, text)

        return ParsedAction(tool_name=tool_name, arguments=arguments)

    # ── Final Answer Extraction ──────────────────────────────────

    def _extract_final_answer(self, text: str) -> Optional[str]:
        """Extract explicit ``<final_answer>``."""
        match = _FINAL_ANSWER_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return None

    def _extract_yield(self, text: str) -> Optional[str]:
        """Extract explicit ``<yield>`` for graceful abort."""
        match = _YIELD_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return None

    def _check_text_leakage(self, text: str) -> None:
        """Ensure no conversational text is leaked outside allowed tags."""
        clean = _THINKING_PATTERN.sub("", text).strip()
        clean = _TITLE_PATTERN.sub("", clean).strip()
        clean = _ACTION_PATTERN.sub("", clean).strip()
        clean = _FINAL_ANSWER_PATTERN.sub("", clean).strip()
        clean = _YIELD_PATTERN.sub("", clean).strip()

        # Strip out prompt template regurgitation if any
        clean = clean.replace(
            "// STOP HERE. Let the tool execute natively. DO NOT write <final_answer>.",
            "",
        ).strip()
        clean = clean.replace("SCENARIO 1", "").replace("SCENARIO 2", "").strip()

        if clean and len(clean) > 10:
            raise SchemaValidationError(
                "Found raw text outside of allowed tags. "
                "You must not output conversational text or explanations outside of "
                "<title>, <thinking>, <action>, <final_answer>, or <yield> tags.",
                raw_response=text,
            )

    # ── XML Args Parsing ─────────────────────────────────────────

    def _parse_xml_args(
        self, raw: str, tool_name: str, full_text: str
    ) -> dict[str, Any]:
        """Parse XML arguments from an ``<args>...</args>`` fragment.

        Expected format:
            <args><path>README.md</path><start_line>1</start_line></args>
        """
        if not raw:
            return {}

        try:
            root = ET.fromstring(f"<root>{raw}</root>")
        except ET.ParseError:
            raise SchemaValidationError(
                f"Invalid XML in <args> for tool '{tool_name}'. Raw content: {raw[:200]}",
                raw_response=full_text,
            )

        # Enforce child-tag arguments (not plain text blobs).
        if not list(root):
            if (root.text or "").strip():
                raise SchemaValidationError(
                    f"Invalid XML args for tool '{tool_name}'. "
                    "Use child tags inside <args>, for example "
                    "<args><path>file.txt</path></args>.",
                    raw_response=full_text,
                )
            return {}

        parsed: dict[str, Any] = {}
        for child in root:
            value = self._xml_element_to_value(child)
            if child.tag in parsed:
                existing = parsed[child.tag]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    parsed[child.tag] = [existing, value]
            else:
                parsed[child.tag] = value

        return parsed

    def _xml_element_to_value(self, elem: ET.Element) -> Any:
        """Recursively convert XML elements into Python primitives."""
        children = list(elem)
        if not children:
            return self._coerce_scalar((elem.text or "").strip())

        # Conventional list form: <items><item>..</item><item>..</item></items>
        if all(child.tag == "item" for child in children):
            return [self._xml_element_to_value(child) for child in children]

        obj: dict[str, Any] = {}
        for child in children:
            value = self._xml_element_to_value(child)
            if child.tag in obj:
                existing = obj[child.tag]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    obj[child.tag] = [existing, value]
            else:
                obj[child.tag] = value

        return obj

    @staticmethod
    def _coerce_scalar(text: str) -> Any:
        """Coerce simple scalar strings to bool/int/float/null where possible."""
        if text == "":
            return ""

        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "none"}:
            return None

        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except ValueError:
                return text

        if re.fullmatch(r"-?\d+\.\d+", text):
            try:
                return float(text)
            except ValueError:
                return text

        return text
