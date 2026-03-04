"""Protocol models for JSON-based agent/system communication.

Typed protocol payloads for the JSON-only runtime path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, TypeGuard
from uuid import uuid4


def _utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _message_id() -> str:
    """Return a stable protocol message identifier."""
    return f"msg_{uuid4().hex}"


def _is_dataclass_instance(value: Any) -> TypeGuard[Any]:
    """True only for dataclass instances (not dataclass types)."""
    return is_dataclass(value) and not isinstance(value, type)


class ProtocolVersion(str, Enum):
    """Supported protocol schema versions."""

    V1_0 = "1.0"


class ProtocolMessageType(str, Enum):
    """Canonical message envelope `type` values."""

    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETION = "completion"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    CANCEL = "cancel"
    CLARIFY = "clarify"
    STATUS_UPDATE = "status_update"


class DecisionType(str, Enum):
    """Decision types for prompt-mode JSON responses."""

    REFLECT = "reflect"
    EXECUTE_ACTION = "execute_action"
    NOTIFY_USER = "notify_user"
    YIELD = "yield"


@dataclass
class ToolCallPayload:
    """Payload for `tool_call` messages."""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultPayload:
    """Payload for `tool_result` messages."""

    ref_id: str = ""
    status: str = "ok"
    output: str = ""


@dataclass
class CompletionPayload:
    """Payload for `completion` messages."""

    result: str
    reasoning: str = ""


@dataclass
class ErrorPayload:
    """Canonical machine-readable error payload."""

    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    recoverable: bool = True


@dataclass
class DecisionPayload:
    """Decision payload used by prompt-mode JSON responses."""

    type: DecisionType
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def is_valid(self) -> bool:
        """Whether the payload satisfies minimum structural rules."""
        if self.type == DecisionType.EXECUTE_ACTION:
            return bool(self.tool.strip())
        if self.type in (DecisionType.NOTIFY_USER, DecisionType.YIELD):
            return bool(self.message.strip())
        return True


@dataclass
class AgentPromptResponse:
    """Top-level JSON structure expected from prompt-mode model output."""

    title: str = ""
    thought: str = ""
    decision: DecisionPayload = field(
        default_factory=lambda: DecisionPayload(type=DecisionType.REFLECT)
    )


@dataclass
class MessageEnvelope:
    """Canonical envelope for protocol messages."""

    type: ProtocolMessageType
    payload: Dict[str, Any]
    id: str = field(default_factory=_message_id)
    version: str = ProtocolVersion.V1_0.value
    timestamp: str = field(default_factory=_utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize envelope as a JSON-ready dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "version": self.version,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def create(
        cls,
        message_type: ProtocolMessageType,
        payload: Any,
        *,
        metadata: Dict[str, Any] | None = None,
        version: str = ProtocolVersion.V1_0.value,
    ) -> "MessageEnvelope":
        """Build an envelope from dataclass or mapping payloads."""
        if _is_dataclass_instance(payload):
            payload_dict = asdict(payload)
        elif isinstance(payload, dict):
            payload_dict = dict(payload)
        else:
            raise TypeError(
                "payload must be a dataclass instance or dictionary, "
                f"got {type(payload).__name__}"
            )

        return cls(
            type=message_type,
            payload=payload_dict,
            metadata=dict(metadata or {}),
            version=version,
        )
