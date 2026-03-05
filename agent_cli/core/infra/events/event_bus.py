"""
Event Bus – the central nervous system of Agent CLI.

All inter-component communication flows through the Event Bus.
No component holds a direct reference to another; they only know
how to ``publish()`` / ``emit()`` / ``subscribe()`` / ``unsubscribe()``.

Two dispatch modes are provided:

* **publish(event)** — *synchronous*: awaits every subscriber in priority
  order.  Use for state changes, task delegation, approval flows.
* **emit(event)** — *fire-and-forget*: schedules subscribers as background
  ``asyncio.Task`` objects and returns immediately.  Use for streaming
  messages, terminal logs, status updates.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Awaitable, Callable

from agent_cli.core.infra.events.events import BaseEvent, SystemErrorEvent

logger = logging.getLogger(__name__)

# ── Type Aliases ─────────────────────────────────────────────────────

EventCallback = Callable[[BaseEvent], Awaitable[None]]


# ══════════════════════════════════════════════════════════════════════
# Abstract Interface
# ══════════════════════════════════════════════════════════════════════


class AbstractEventBus(ABC):
    """Central message broker.

    Components publish events; subscribers receive them by ``event_type``.
    """

    @abstractmethod
    async def publish(self, event: BaseEvent) -> None:
        """Synchronous dispatch – awaits ALL callbacks in priority order.

        The publisher is blocked until every subscriber has finished
        processing.  If a subscriber raises, the error is caught and a
        ``SystemErrorEvent`` is emitted; remaining subscribers still
        execute.
        """

    @abstractmethod
    async def emit(self, event: BaseEvent) -> None:
        """Fire-and-forget dispatch – schedules callbacks as background tasks.

        The publisher is **not** blocked.  Errors in background tasks are
        caught and emitted as ``SystemErrorEvent``.
        """

    @abstractmethod
    def subscribe(
        self,
        event_type: str,
        callback: EventCallback,
        priority: int = 0,
    ) -> str:
        """Register a callback for a specific event type.

        Args:
            event_type: The event class name (e.g. ``"UserRequestEvent"``).
            callback:   An async function accepting a ``BaseEvent`` subclass.
            priority:   Lower number = processed first.
                        Recommended bands: 0 State Manager, 10 Orchestrator,
                        50 TUI.

        Returns:
            A unique ``subscription_id`` for later unsubscription.
        """

    @abstractmethod
    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by its ID.  Safe to call multiple times."""

    @abstractmethod
    async def drain(self) -> None:
        """Graceful shutdown.

        1. Sets the bus to DRAINING (rejects new publish/emit calls).
        2. Waits for all in-flight background tasks from ``emit()``.
        3. Sets the bus to STOPPED.
        """


# ══════════════════════════════════════════════════════════════════════
# Concrete Implementation
# ══════════════════════════════════════════════════════════════════════


class BusState(Enum):
    """Lifecycle state of the event bus."""

    RUNNING = auto()
    DRAINING = auto()
    STOPPED = auto()


@dataclass
class _Subscription:
    """Internal record of a single subscriber registration."""

    id: str
    event_type: str
    callback: EventCallback
    priority: int  # Lower number = higher priority


class AsyncEventBus(AbstractEventBus):
    """Production implementation using topic-based routing with dual dispatch."""

    def __init__(self) -> None:
        # Registry: event_type -> list of subscriptions
        self._subscriptions: dict[str, list[_Subscription]] = defaultdict(list)

        # Background tasks from emit() - tracked for graceful shutdown
        self._background_tasks: set[asyncio.Task] = set()

        # Bus lifecycle state
        self._state: BusState = BusState.RUNNING

    # ── Dispatch ─────────────────────────────────────────────────

    async def publish(self, event: BaseEvent) -> None:
        """Synchronous dispatch — awaits all callbacks in priority order."""

        if self._state != BusState.RUNNING:
            logger.warning(
                "Bus is %s; dropping publish(%s)",
                self._state.name,
                event.event_type,
            )
            return

        for sub in self._get_sorted_subscriptions(event.event_type):
            await self._safe_invoke(sub, event)

    async def emit(self, event: BaseEvent) -> None:
        """Fire-and-forget — schedules callbacks as background tasks."""

        if self._state != BusState.RUNNING:
            logger.warning(
                "Bus is %s; dropping emit(%s)",
                self._state.name,
                event.event_type,
            )
            return

        for sub in self._get_sorted_subscriptions(event.event_type):
            task = asyncio.create_task(
                self._safe_invoke(sub, event),
                name=f"event:{event.event_type}→{sub.id}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    # ── Subscription Management ──────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        callback: EventCallback,
        priority: int = 0,
    ) -> str:
        sub_id = str(uuid.uuid4())
        self._subscriptions[event_type].append(
            _Subscription(
                id=sub_id,
                event_type=event_type,
                callback=callback,
                priority=priority,
            )
        )
        logger.debug(
            "Subscribed %s to %s (priority=%d)", sub_id, event_type, priority
        )
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        for event_type, subs in self._subscriptions.items():
            before = len(subs)
            self._subscriptions[event_type] = [
                s for s in subs if s.id != subscription_id
            ]
            if len(self._subscriptions[event_type]) < before:
                logger.debug(
                    "Unsubscribed %s from %s", subscription_id, event_type
                )
                return  # IDs are unique; stop after first match

    # ── Lifecycle ────────────────────────────────────────────────

    async def drain(self) -> None:
        """Graceful shutdown: stop accepting, wait for in-flight tasks."""

        self._state = BusState.DRAINING
        logger.info(
            "Event Bus draining... %d tasks pending",
            len(self._background_tasks),
        )

        if self._background_tasks:
            await asyncio.gather(
                *self._background_tasks, return_exceptions=True
            )

        self._state = BusState.STOPPED
        logger.info("Event Bus stopped.")

    # ── Queries (useful for testing / debugging) ─────────────────

    @property
    def state(self) -> BusState:
        """Current lifecycle state of the bus."""
        return self._state

    @property
    def pending_task_count(self) -> int:
        """Number of in-flight background tasks."""
        return len(self._background_tasks)

    def subscriber_count(self, event_type: str) -> int:
        """Number of subscribers for a given event type."""
        return len(self._subscriptions.get(event_type, []))

    # ── Internal Helpers ─────────────────────────────────────────

    def _get_sorted_subscriptions(
        self, event_type: str
    ) -> list[_Subscription]:
        """Return subscriptions sorted by priority (ascending)."""
        return sorted(
            self._subscriptions.get(event_type, []),
            key=lambda s: s.priority,
        )

    async def _safe_invoke(
        self, sub: _Subscription, event: BaseEvent
    ) -> None:
        """Invoke a subscriber with full error isolation.

        On failure: log the error AND emit a ``SystemErrorEvent``.
        Guard against infinite recursion by skipping error emission
        if the failing event is itself a ``SystemErrorEvent``.
        """
        try:
            await sub.callback(event)
        except Exception as e:
            logger.error(
                "Subscriber %s failed on %s: %s",
                sub.id,
                event.event_type,
                e,
                exc_info=True,
            )
            # Prevent infinite recursion
            if event.event_type != "SystemErrorEvent":
                error_event = SystemErrorEvent(
                    source="event_bus",
                    error_message=str(e),
                    original_event_type=event.event_type,
                    subscriber_id=sub.id,
                )
                await self.emit(error_event)
