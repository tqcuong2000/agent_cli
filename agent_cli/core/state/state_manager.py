"""
State Manager — the single source of truth for task lifecycle.

Enforces a strict Finite State Machine (FSM), validates that all
transitions are legal, prevents race conditions via per-task locking,
and automatically publishes ``StateChangeEvent`` to the Event Bus
on every transition.

No component may change a task's state outside the State Manager.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Protocol

from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import StateChangeEvent
from agent_cli.core.state.state_models import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    TaskRecord,
    TaskState,
)
from agent_cli.core.tracing import start_span

logger = logging.getLogger(__name__)


class SupportsTaskObservability(Protocol):
    """Minimal observability hooks used by the state manager."""

    def record_task_created(self) -> None: ...

    def record_task_result(self, *, is_success: bool) -> None: ...


# ══════════════════════════════════════════════════════════════════════
# Abstract Interface (1.2.1)
# ══════════════════════════════════════════════════════════════════════


class AbstractStateManager(ABC):
    """The single source of truth for task lifecycle state.

    All state mutations go through this interface.
    """

    @abstractmethod
    async def create_task(
        self,
        description: str,
        parent_id: Optional[str] = None,
        assigned_agent: str = "",
    ) -> TaskRecord:
        """Register a new task in ``PENDING`` state.

        If *parent_id* is provided the task is linked as a child.
        Publishes ``StateChangeEvent(from_state="", to_state="PENDING")``.
        """

    @abstractmethod
    async def transition(
        self,
        task_id: str,
        to_state: TaskState,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TaskRecord:
        """Attempt to move a task to a new state.

        Flow:
        1. Acquire the task's ``asyncio.Lock``.
        2. Validate: is ``current → to_state`` in ``VALID_TRANSITIONS``?
        3. Invalid → raise ``InvalidTransitionError`` (does **not** publish).
        4. Valid → update state, record timestamp and history entry.
        5. Publish ``StateChangeEvent`` to the Event Bus.
        6. Release lock and return the updated ``TaskRecord``.

        Args:
            result: Populated when transitioning to ``SUCCESS``.
            error:  Populated when transitioning to ``FAILED``.
        """

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Retrieve a task by ID.  Returns ``None`` if not found."""

    @abstractmethod
    def get_children(self, parent_id: str) -> List[TaskRecord]:
        """Retrieve all child tasks of an ExecutionPlan parent."""

    @abstractmethod
    def get_active_tasks(self) -> List[TaskRecord]:
        """Return all tasks in non-terminal states."""

    @abstractmethod
    async def cancel_task_tree(self, task_id: str) -> None:
        """Cancel a task and all its active descendants (depth-first)."""


# ══════════════════════════════════════════════════════════════════════
# Concrete Implementation (1.2.2 + 1.2.4)
# ══════════════════════════════════════════════════════════════════════


class TaskStateManager(AbstractStateManager):
    """In-memory state manager with per-task locking and auto-publish
    to the Event Bus.
    """

    def __init__(
        self,
        event_bus: AbstractEventBus,
        observability: SupportsTaskObservability | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._observability = observability

        # Task registry: task_id -> TaskRecord
        self._tasks: dict[str, TaskRecord] = {}

        # Per-task locks for concurrent transition safety
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Task Creation ────────────────────────────────────────────

    async def create_task(
        self,
        description: str,
        parent_id: Optional[str] = None,
        assigned_agent: str = "",
    ) -> TaskRecord:
        span = start_span("state_transition")
        task = TaskRecord(
            description=description,
            parent_id=parent_id,
            assigned_agent=assigned_agent,
        )
        task.history.append(
            {
                "from": None,
                "to": TaskState.PENDING.name,
                "timestamp": task.created_at,
            }
        )

        self._tasks[task.task_id] = task
        self._locks[task.task_id] = asyncio.Lock()

        # Link to parent if applicable
        if parent_id and parent_id in self._tasks:
            self._tasks[parent_id].children_ids.append(task.task_id)

        # Publish creation event (State → Event Bus bridge)
        await self._event_bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id=task.task_id,
                from_state="",
                to_state=TaskState.PENDING.name,
            )
        )
        timing = span.finish()

        if self._observability is not None:
            self._observability.record_task_created()

        logger.info(
            "Task created",
            extra={
                "source": "state_manager",
                "task_id": task.task_id,
                "span_id": timing["span_id"],
                "span_type": timing["span_type"],
                "data": {
                    "description_preview": description[:50],
                    "assigned_agent": assigned_agent,
                    "duration_ms": timing["duration_ms"],
                },
            },
        )
        return task

    # ── State Transitions ────────────────────────────────────────

    async def transition(
        self,
        task_id: str,
        to_state: TaskState,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TaskRecord:
        span = start_span("state_transition", task_id=task_id)
        if task_id not in self._tasks:
            span.finish()
            raise KeyError(f"Task '{task_id}' not found in state manager.")

        async with self._locks[task_id]:
            task = self._tasks[task_id]
            from_state = task.state

            # ── Validate transition ──
            if to_state not in VALID_TRANSITIONS.get(from_state, set()):
                span.finish()
                raise InvalidTransitionError(task_id, from_state, to_state)

            # ── Commit transition ──
            task.state = to_state
            task.updated_at = time.time()
            task.history.append(
                {
                    "from": from_state.name,
                    "to": to_state.name,
                    "timestamp": task.updated_at,
                }
            )

            # ── Attach terminal data ──
            if to_state == TaskState.SUCCESS and result is not None:
                task.result = result
            if to_state == TaskState.FAILED and error is not None:
                task.error = error

        # Auto-publish outside the lock to avoid deadlocks (1.2.4)
        await self._event_bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id=task_id,
                from_state=from_state.name,
                to_state=to_state.name,
            )
        )
        timing = span.finish()

        if self._observability is not None:
            if to_state == TaskState.SUCCESS:
                self._observability.record_task_result(is_success=True)
            elif to_state == TaskState.FAILED:
                self._observability.record_task_result(is_success=False)

        logger.info(
            "Task transition",
            extra={
                "source": "state_manager",
                "task_id": task_id,
                "span_id": timing["span_id"],
                "span_type": timing["span_type"],
                "data": {
                    "from_state": from_state.name,
                    "to_state": to_state.name,
                    "duration_ms": timing["duration_ms"],
                },
            },
        )

        return task

    # ── Queries ──────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def get_children(self, parent_id: str) -> List[TaskRecord]:
        parent = self._tasks.get(parent_id)
        if not parent:
            return []
        return [self._tasks[cid] for cid in parent.children_ids if cid in self._tasks]

    def get_active_tasks(self) -> List[TaskRecord]:
        return [t for t in self._tasks.values() if not t.is_terminal]

    # ── Cancellation Cascade ─────────────────────────────────────

    async def cancel_task_tree(self, task_id: str) -> None:
        """Cancel a task and all its active descendants (depth-first)."""
        task = self.get_task(task_id)
        if not task or task.is_terminal:
            return

        # Cancel children first (depth-first)
        for child in self.get_children(task_id):
            await self.cancel_task_tree(child.task_id)

        # Then cancel the task itself
        try:
            await self.transition(task_id, TaskState.CANCELLED)
        except InvalidTransitionError:
            pass  # Already terminal (race condition — safe to ignore)
