"""
Event system for Agent CLI.

All components communicate exclusively through typed events published
on the Event Bus.  Every event inherits from ``BaseEvent`` which
provides a unique ID, a monotonic timestamp and a human-readable
source tag used for debugging/tracing (never for routing).

Routing is **topic-based**: the Event Bus uses ``event_type``
(derived from the Python class name) as the routing key.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Base Event ───────────────────────────────────────────────────────


@dataclass
class BaseEvent:
    """Root class for every event in the system.

    All events are *immutable* dataclasses with an auto-generated ID and
    timestamp.  Concrete event subclasses add domain-specific fields.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # Human-readable origin, e.g. "orchestrator"

    @property
    def event_type(self) -> str:
        """Routing key – derived from the concrete class name."""
        return self.__class__.__name__


# ═══════════════════════════════════════════════════════════════════════
# A. CORE LIFECYCLE EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class UserRequestEvent(BaseEvent):
    """Published by the TUI when the user submits a prompt."""

    text: str = ""
    injected_context: str = ""  # Populated by the @-prefix pre-processor


@dataclass
class StateChangeEvent(BaseEvent):
    """Published automatically by the State Manager on every transition."""

    task_id: str = ""
    from_state: str = ""
    to_state: str = ""


@dataclass
class SystemShutdownEvent(BaseEvent):
    """Emitted by the TUI (/exit) or signal handler to trigger graceful shutdown."""

    reason: str = ""


@dataclass
class SystemErrorEvent(BaseEvent):
    """Emitted by the Event Bus when a subscriber callback raises.

    The infinite-recursion guard ensures this event is never re-emitted
    if its own processing fails.
    """

    error_message: str = ""
    original_event_type: str = ""  # Which event processing caused the failure
    subscriber_id: str = ""  # Which subscriber failed


# ═══════════════════════════════════════════════════════════════════════
# B. AGENT EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TaskDelegatedEvent(BaseEvent):
    """Published by the Orchestrator when it assigns a task to an Agent."""

    task_id: str = ""
    agent_name: str = ""
    description: str = ""


@dataclass
class TaskResultEvent(BaseEvent):
    """Published by an Agent when it finishes a task."""

    task_id: str = ""
    result: str = ""
    is_success: bool = True


@dataclass
class AgentMessageEvent(BaseEvent):
    """Streamed from Agents to the TUI for real-time display."""

    agent_name: str = ""
    content: str = ""
    is_monologue: bool = False  # True = <thinking>, False = user-facing


# ═══════════════════════════════════════════════════════════════════════
# C. TOOL EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ToolExecutionStartEvent(BaseEvent):
    """Emitted when a tool begins execution."""

    task_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionResultEvent(BaseEvent):
    """Published when a tool finishes execution."""

    task_id: str = ""
    tool_name: str = ""
    output: str = ""
    is_error: bool = False


# ═══════════════════════════════════════════════════════════════════════
# D. INTERACTION EVENTS (Human-in-the-Loop)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class UserApprovalRequestEvent(BaseEvent):
    """Published when a tool requires user approval before execution."""

    task_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    risk_description: str = ""


@dataclass
class UserApprovalResponseEvent(BaseEvent):
    """Published by the TUI after the user responds to an approval request."""

    task_id: str = ""
    approved: bool = False
    modified_arguments: Optional[Dict[str, Any]] = None


@dataclass
class AgentQuestionRequestEvent(BaseEvent):
    """Published when the agent asks a clarification question."""

    task_id: str = ""
    question: str = ""
    options: List[str] = field(default_factory=list)  # 2-5 suggested answers


@dataclass
class AgentQuestionResponseEvent(BaseEvent):
    """Published by the TUI after user answers an agent question."""

    task_id: str = ""
    answer: str = ""


# ═══════════════════════════════════════════════════════════════════════
# E. TERMINAL EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TerminalSpawnedEvent(BaseEvent):
    """Emitted when a new persistent terminal is created."""

    terminal_id: str = ""
    command: str = ""


@dataclass
class TerminalLogEvent(BaseEvent):
    """Streamed log lines from a persistent terminal."""

    terminal_id: str = ""
    content: str = ""


@dataclass
class TerminalExitedEvent(BaseEvent):
    """Emitted when a persistent terminal process exits."""

    terminal_id: str = ""
    exit_code: int = 0


# ═══════════════════════════════════════════════════════════════════════
# F. ERROR EVENTS (task-level, see also SystemErrorEvent above)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TaskErrorEvent(BaseEvent):
    """Published by the Orchestrator when a task encounters an error."""

    task_id: str = ""
    tier: str = ""  # "TRANSIENT" | "RECOVERABLE" | "FATAL"
    error_message: str = ""  # User-friendly
    technical_detail: str = ""  # Full error string (debug logs only)


@dataclass
class RetryAttemptEvent(BaseEvent):
    """Published when a transient error triggers an automatic retry."""

    task_id: str = ""
    attempt: int = 0
    max_retries: int = 0
    delay_seconds: float = 0.0
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════
# G. FILE / WORKSPACE EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class FileChangedEvent(BaseEvent):
    """Published by the FileChangeTracker when a file is created/modified/deleted."""

    file_path: str = ""
    change_type: str = ""  # "created" | "modified" | "deleted"
    agent_name: str = ""


@dataclass
class ChangesResetEvent(BaseEvent):
    """Published when the Orchestrator resets the changed-files panel."""

    task_id: str = ""


# ═══════════════════════════════════════════════════════════════════════
# H. SESSION / PLAN EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SessionSavedEvent(BaseEvent):
    """Published after a session is persisted to disk."""

    session_id: str = ""


@dataclass
class PlanReadyEvent(BaseEvent):
    """Published by the Planner Agent when an ExecutionPlan is ready for review."""

    task_id: str = ""
    plan_summary: str = ""
    steps: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# I. CONFIGURATION EVENTS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SettingsChangedEvent(BaseEvent):
    """Published when a setting is changed at runtime (e.g. /effort)."""

    setting_name: str = ""
    new_value: Any = None
