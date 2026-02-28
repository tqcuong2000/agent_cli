"""
Schema Validator — dual-mode LLM response validation.

Validates and normalizes every ``LLMResponse`` into an ``AgentResponse``
before the Agent loop processes it.  Supports two modes:

* **Native FC** (``ToolCallMode.NATIVE``) — structured tool calls
  already parsed by the provider API.  Validation is a safety check.
* **XML Prompting** (``ToolCallMode.XML``) — parse ``<action>`` tags
  from raw text, coerce malformed JSON, handle missing tags.

Both modes share ``<thinking>`` extraction and ``<final_answer>``
detection logic.

See ``02_schema_verification.md`` for the full specification.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Optional, Set

from agent_cli.agent.parsers import AgentResponse, ParsedAction
from agent_cli.core.error_handler.errors import SchemaValidationError
from agent_cli.providers.models import LLMResponse, ToolCallMode

logger = logging.getLogger(__name__)

# ── Precompiled regex patterns ───────────────────────────────────────

_THINKING_PATTERN = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", re.DOTALL)
_TOOL_PATTERN = re.compile(r"<tool>(.*?)</tool>", re.DOTALL)
_ARGS_PATTERN = re.compile(r"<args>(.*?)</args>", re.DOTALL)
_FINAL_ANSWER_PATTERN = re.compile(r"<final_answer>(.*?)</final_answer>", re.DOTALL)


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

    def __init__(self, registered_tools: list[str]) -> None:
        self._registered_tools: Set[str] = set(registered_tools)

    # ── Public API ───────────────────────────────────────────────

    def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
        """Parse and validate an LLM response (mode-aware)."""

        # ── Step 1: Extract thinking (same for both modes) ───────
        thinking = self.extract_thinking(response.text_content)

        # ── Step 2: Mode-specific action parsing ─────────────────
        if response.tool_mode == ToolCallMode.NATIVE:
            action = self._parse_native_fc(response)
        else:
            action = self._parse_xml_prompting(response.text_content)

        # ── Step 3: Check for final answer ───────────────────────
        final_answer: Optional[str] = None
        if action is None:
            final_answer = self._extract_final_answer(response.text_content)

        # ── Step 4: Enforce required reasoning envelope ──────────
        if (action is not None or final_answer is not None) and not thinking:
            raise SchemaValidationError(
                "Response includes action/final answer but is missing "
                "required <title> and <thinking> reasoning block.",
                raw_response=response.text_content,
            )

        # ── Step 5: Validate at least one output exists ──────────
        if action is None and final_answer is None and not thinking:
            raise SchemaValidationError(
                "Response contains no <thinking>, no tool call, and no "
                "final answer. Please respond with either a tool action "
                "or a final answer.",
                raw_response=response.text_content,
            )

        return AgentResponse(
            thought=thinking,
            action=action,
            final_answer=final_answer,
        )

    def extract_thinking(self, text: str) -> str:
        """Extract and validate ``<title>`` + ``<thinking>`` payload.

        If multiple ``<thinking>`` blocks exist, they are concatenated
        with newlines (some models split reasoning across blocks).
        Returns normalized XML payload:
        ``<title>...</title>\\n<thinking>...</thinking>``.
        """
        matches = _THINKING_PATTERN.findall(text)
        if not matches:
            return ""

        thoughts = "\n".join(m.strip() for m in matches if m.strip()).strip()
        if not thoughts:
            return ""

        title_match = _TITLE_PATTERN.search(text)
        if not title_match:
            raise SchemaValidationError(
                "Missing <title> for <thinking>. "
                "Provide a short title with 4 to 12 words.",
                raw_response=text,
            )

        raw_title = title_match.group(1).strip()
        title_words = [w for w in raw_title.split() if w]
        if not 4 <= len(title_words) <= 12:
            raise SchemaValidationError(
                "Invalid <title> length. Title must be 4 to 12 words.",
                raw_response=text,
            )

        title = " ".join(title_words)
        return f"<title>{title}</title>\n<thinking>{thoughts}</thinking>"

    # ── Native FC Parsing (Trivial — already structured) ─────────

    def _parse_native_fc(self, response: LLMResponse) -> Optional[ParsedAction]:
        """Parse structured tool calls from native function calling.

        The API already enforced the schema, so this is mostly a
        safety check that the tool name exists in our registry.
        """
        if not response.tool_calls:
            return None

        # Take the first tool call (multi-tool is a future extension)
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

    # ── XML Prompting Parsing (Complex — needs coercion) ─────────

    def _parse_xml_prompting(self, text: str) -> Optional[ParsedAction]:
        """Parse ``<action>`` XML tags from raw text output.

        Includes coercion for common LLM formatting errors.
        """
        # Look for <action>...</action> block
        action_match = _ACTION_PATTERN.search(text)
        if not action_match:
            return None

        action_block = action_match.group(1)

        # ── Extract <tool> name ──────────────────────────────────
        tool_match = _TOOL_PATTERN.search(action_block)
        if not tool_match:
            raise SchemaValidationError(
                "Found <action> block but missing <tool> tag. "
                "Expected format: "
                "<action><tool>name</tool><args>{...}</args></action>",
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

        # ── Extract <args> JSON ──────────────────────────────────
        args_match = _ARGS_PATTERN.search(action_block)
        if not args_match:
            raise SchemaValidationError(
                f"Tool '{tool_name}' is missing <args> block.",
                raw_response=text,
            )

        raw_args = args_match.group(1).strip()
        arguments = self._parse_json_args(raw_args, tool_name, text)

        return ParsedAction(tool_name=tool_name, arguments=arguments)

    # ── Final Answer Extraction ──────────────────────────────────

    def _extract_final_answer(self, text: str) -> Optional[str]:
        """Extract ``<final_answer>`` or treat remaining text as answer.

        Priority:
        1. Explicit ``<final_answer>`` tags (preferred).
        2. Clean text (with ``<title>``/``<thinking>`` removed) as implicit answer.
        """
        match = _FINAL_ANSWER_PATTERN.search(text)
        if match:
            return match.group(1).strip()

        # If no tags, the clean text (minus <title>/<thinking>) might be answer
        clean = _THINKING_PATTERN.sub("", text).strip()
        clean = _TITLE_PATTERN.sub("", clean).strip()
        return clean if clean else None

    # ── JSON Parsing + Coercion ──────────────────────────────────

    def _parse_json_args(self, raw: str, tool_name: str, full_text: str) -> dict:
        """Parse JSON arguments with auto-repair for common issues."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Attempt coercion
        coerced = self._attempt_json_coercion(raw)
        if coerced is not None:
            logger.warning("Coerced malformed JSON args for tool '%s'", tool_name)
            return coerced

        raise SchemaValidationError(
            f"Invalid JSON in <args> for tool '{tool_name}'. Raw content: {raw[:200]}",
            raw_response=full_text,
        )

    @staticmethod
    def _attempt_json_coercion(raw: str) -> Optional[dict]:
        """Attempt to fix common JSON formatting errors from LLMs.

        Tries:
        1. Replace single quotes with double quotes.
        2. Remove trailing commas before ``}`` or ``]``.
        3. Combined fix (both).
        """
        # Strategy 1: Single quotes → double quotes
        try:
            fixed = raw.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Remove trailing commas
        try:
            fixed = re.sub(r",\s*}", "}", raw)
            fixed = re.sub(r",\s*]", "]", fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Both fixes combined
        try:
            fixed = raw.replace("'", '"')
            fixed = re.sub(r",\s*}", "}", fixed)
            fixed = re.sub(r",\s*]", "]", fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        return None
