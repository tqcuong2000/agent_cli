"""
Provider Data Models — normalized request/response types for all LLM providers.

These models decouple the Agent loop from specific API formats.
The Agent only ever constructs ``LLMRequest`` and receives ``LLMResponse``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════


class ToolCallMode(str, Enum):
    """How tool calls were delivered in this response."""

    NATIVE = "NATIVE"  # Structured JSON from native function calling API
    PROMPT_JSON = "PROMPT_JSON"  # Parsed from prompt-mode JSON text output

    @classmethod
    def _missing_(cls, value: object) -> "ToolCallMode | None":
        """Map serialized values to enum members."""
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized == "PROMPT_JSON":
                return cls.PROMPT_JSON
            if normalized == "NATIVE":
                return cls.NATIVE
        return None


class MessageRole(str, Enum):
    """Standard roles for conversation messages."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(str, Enum):
    """Why the model stopped generating."""

    END_TURN = "end_turn"  # Natural completion
    TOOL_USE = "tool_use"  # Model wants to call a tool
    MAX_TOKENS = "max_tokens"  # Hit the token limit
    STOP_SEQUENCE = "stop_sequence"


# ══════════════════════════════════════════════════════════════════════
# Tool Call
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ToolCall:
    """A single tool invocation extracted from the LLM response."""

    tool_name: str
    arguments: Dict[str, Any]
    mode: ToolCallMode = ToolCallMode.NATIVE
    native_call_id: str = ""  # Provider's call ID (for FC response pairing)


# ══════════════════════════════════════════════════════════════════════
# LLM Request (2.1.2)
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Message:
    """A single message in the conversation history."""

    role: MessageRole
    content: str
    tool_call_id: str = ""  # If role == TOOL, the call this responds to
    tool_calls: List[ToolCall] = field(
        default_factory=list
    )  # If role == ASSISTANT with FC


@dataclass
class LLMRequest:
    """Normalized request to any LLM provider.

    The Agent loop constructs this; the provider adapter translates it
    into the vendor-specific API format.
    """

    messages: List[Message]
    model: str = ""
    tools: Optional[List[Dict[str, Any]]] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    stop_sequences: Optional[List[str]] = None
    stream: bool = True

    def to_message_dicts(self) -> List[Dict[str, Any]]:
        """Convert messages to simple dicts for provider adapters."""
        result = []
        for msg in self.messages:
            d: Dict[str, Any] = {"role": msg.role.value, "content": msg.content}
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            result.append(d)
        return result


# ══════════════════════════════════════════════════════════════════════
# LLM Response (2.1.3)
# ══════════════════════════════════════════════════════════════════════


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider.

    The Agent and Schema Validator only ever see this object.
    """

    # ── Content ──────────────────────────────────────────────────
    text_content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_mode: ToolCallMode = ToolCallMode.PROMPT_JSON

    # ── Token usage ──────────────────────────────────────────────
    input_tokens: int = 0
    output_tokens: int = 0

    # ── Cost estimation (USD) ────────────────────────────────────
    cost_usd: float = 0.0

    # ── Provider metadata ────────────────────────────────────────
    model: str = ""
    provider: str = ""
    stop_reason: StopReason = StopReason.END_TURN

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def is_final_answer(self) -> bool:
        """True if the response declares a final user-facing decision."""
        if self.has_tool_calls:
            return False
        try:
            payload = json.loads(self.text_content)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        decision = payload.get("decision")
        if not isinstance(decision, dict):
            return False
        decision_type = str(decision.get("type", "")).strip().lower()
        return decision_type in {"notify_user", "yield"}


# ══════════════════════════════════════════════════════════════════════
# Stream Chunk (used by streaming interface)
# ══════════════════════════════════════════════════════════════════════


@dataclass
class StreamChunk:
    """A single chunk emitted during streaming.

    The TUI subscribes to these for progressive display.
    """

    text: str = ""
    is_thinking: bool = False  # True if this chunk belongs to reasoning text
    is_tool_call: bool = False  # True if this is a tool call block (buffered)
    is_final: bool = False  # True for the last chunk in the stream

    # Only populated on the final chunk
    usage: Optional[Dict[str, int]] = (
        None  # {"input_tokens": ..., "output_tokens": ...}
    )


@dataclass
class ProviderRequestOptions:
    """Provider-managed request options derived from agent capabilities."""

    provider_managed_tools: List[str] = field(default_factory=list)

    @property
    def web_search_enabled(self) -> bool:
        tools = {str(name).strip().lower() for name in self.provider_managed_tools}
        return "web_search" in tools
