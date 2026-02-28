"""
State models for the task lifecycle FSM.

Defines the ``TaskState`` enum, valid transitions, and the
``TaskRecord`` dataclass that tracks every task in the system.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional


# ══════════════════════════════════════════════════════════════════════
# Task State Enum
# ══════════════════════════════════════════════════════════════════════


class TaskState(Enum):
    """All possible states in the task lifecycle."""

    PENDING = auto()  # Registered but not started
    ROUTING = auto()  # Orchestrator analysing the prompt
    WORKING = auto()  # Agent actively in its ReAct loop
    AWAITING_INPUT = auto()  # Paused — waiting for user approval / clarification
    SUCCESS = auto()  # Terminal — completed successfully
    FAILED = auto()  # Terminal — unrecoverable error or max iterations
    CANCELLED = auto()  # Terminal — user explicitly cancelled


TERMINAL_STATES: set[TaskState] = {
    TaskState.SUCCESS,
    TaskState.FAILED,
    TaskState.CANCELLED,
}

ACTIVE_STATES: set[TaskState] = {
    TaskState.PENDING,
    TaskState.ROUTING,
    TaskState.WORKING,
    TaskState.AWAITING_INPUT,
}


# ══════════════════════════════════════════════════════════════════════
# Formal Transition Table
# ══════════════════════════════════════════════════════════════════════

VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.ROUTING, TaskState.CANCELLED},
    TaskState.ROUTING: {TaskState.WORKING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.WORKING: {
        TaskState.AWAITING_INPUT,
        TaskState.SUCCESS,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.AWAITING_INPUT: {TaskState.WORKING, TaskState.CANCELLED},
    # Terminal states — no outgoing transitions
    TaskState.SUCCESS: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


# ══════════════════════════════════════════════════════════════════════
# Task Record
# ══════════════════════════════════════════════════════════════════════


@dataclass
class TaskRecord:
    """A single tracked task in the system.

    Can be a top-level task or a child of an ExecutionPlan.
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: TaskState = TaskState.PENDING

    # Descriptive metadata
    description: str = ""
    assigned_agent: str = ""  # e.g. "coder_agent", "researcher_agent"

    # Parent-Child relationship
    parent_id: Optional[str] = None  # None = top-level task
    children_ids: List[str] = field(default_factory=list)

    # Timestamps for auditing
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Transition history (append-only log for debugging)
    history: List[dict] = field(default_factory=list)

    # Result data (populated on terminal state)
    result: Optional[str] = None  # Final output on SUCCESS
    error: Optional[str] = None  # Error message on FAILED

    @property
    def is_terminal(self) -> bool:
        """Whether this task is in a final state."""
        return self.state in TERMINAL_STATES


# ══════════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════════


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
    ) -> None:
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition for task '{task_id}': "
            f"{from_state.name} → {to_state.name}"
        )
