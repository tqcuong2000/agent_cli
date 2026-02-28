# Event Bus Architecture (The Backbone)

## Overview
The Event Bus is the central nervous system of the Agent CLI. Every component — the Orchestrator, Agents, TUI, State Manager, and Terminal Manager — communicates exclusively through it. No component holds a direct reference to another; they only know how to publish and subscribe to standardized events.

This document is the **definitive specification** for the Event Bus, covering its routing strategy, dispatch modes, error handling, subscription lifecycle, and shutdown protocol.

---

## 1. Architectural Role

The Event Bus solves three critical problems:

1. **Decoupling:** The TUI doesn't import Agent classes. Agents don't import TUI widgets. They are connected only by event contracts.
2. **Async Non-Blocking TUI:** The TUI subscribes to events and renders reactively. It never calls `await agent.run()` directly.
3. **Extensibility:** Adding a new feature (file logger, Discord bot, cost tracker) means subscribing to existing events — zero changes to existing code.

### Integration Points

```
┌──────────────┐     publish()      ┌──────────────┐
│     TUI      │ ──────────────────→│              │
│  (Input Bar) │                    │              │
└──────────────┘                    │              │
                                    │              │     subscribe()     ┌──────────────┐
┌──────────────┐     publish()      │   EVENT BUS  │ ──────────────────→│ State Manager│
│  Orchestrator│ ──────────────────→│              │                    └──────────────┘
│              │                    │   publish()  │
└──────────────┘                    │    emit()    │     subscribe()     ┌──────────────┐
                                    │  subscribe() │ ──────────────────→│     TUI      │
┌──────────────┐     emit()         │ unsubscribe()│                    │  (Widgets)   │
│    Agents    │ ──────────────────→│   drain()    │                    └──────────────┘
│  (Workers)   │                    │              │
└──────────────┘                    │              │     subscribe()     ┌──────────────┐
                                    │              │ ──────────────────→│   Agents     │
┌──────────────┐     emit()         │              │                    │  (Workers)   │
│  Terminal    │ ──────────────────→│              │                    └──────────────┘
│  Manager     │                    └──────────────┘
└──────────────┘
```

---

## 2. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Routing** | Topic-based (by `event_type`) | Each component receives only events it cares about. No broadcast filtering overhead. |
| **Dispatch Mode** | Dual: `publish()` (sync) + `emit()` (fire-and-forget) | Critical events (state changes) need ordering guarantees. Streaming events (agent messages, terminal logs) need speed. |
| **Error Isolation** | Catch + Log + Emit `SystemErrorEvent` | One buggy subscriber never crashes the publisher. Other components can react to errors via the error event. |
| **Priority** | Numeric (lower number = higher priority) | The State Manager processes `StateChangeEvent` *before* the TUI renders it. |
| **Unsubscribe** | Yes, via `subscription_id` | Prevents orphaned callbacks when TUI widgets are destroyed. |
| **Wildcard Subscriptions** | No | Keeps routing simple and predictable. Each subscription targets exactly one event type. |
| **Event Type Identity** | Python class name (`__class__.__name__`) | No magic strings. Type safety via class hierarchy. `UserRequestEvent` → `"UserRequestEvent"`. |
| **Shutdown** | `drain()` waits for all background tasks | No lost events on `Ctrl+C`. |

---

## 3. Event Taxonomy

All events inherit from `BaseEvent`. Events are grouped by domain:

### A. Core Lifecycle Events
| Event | Publisher | Subscribers | Dispatch Mode |
|---|---|---|---|
| `UserRequestEvent` | TUI Input | Orchestrator | `publish()` |
| `StateChangeEvent` | State Manager | TUI, Orchestrator, Agents | `publish()` |
| `SystemShutdownEvent` | TUI (`/exit`), Signal Handler | Terminal Manager, State Manager, Event Bus | `publish()` |
| `SystemErrorEvent` | Event Bus (internal) | TUI (Error Panel), Logger | `emit()` |

### B. Agent Events
| Event | Publisher | Subscribers | Dispatch Mode |
|---|---|---|---|
| `TaskDelegatedEvent` | Orchestrator | Target Agent | `publish()` |
| `TaskResultEvent` | Agent (Worker) | Orchestrator | `publish()` |
| `AgentMessageEvent` | Agent (Worker) | TUI (Chat Log) | `emit()` |

### C. Tool Events
| Event | Publisher | Subscribers | Dispatch Mode |
|---|---|---|---|
| `ToolExecutionStartEvent` | Agent Loop | TUI (Status), Logger | `emit()` |
| `ToolExecutionResultEvent` | Tool Executor | Agent Loop | `publish()` |

### D. Interaction Events
| Event | Publisher | Subscribers | Dispatch Mode |
|---|---|---|---|
| `UserApprovalRequestEvent` | Tool Executor (`is_safe=False`) | TUI (Modal Popup) | `publish()` |
| `UserApprovalResponseEvent` | TUI (Modal Popup) | Agent Loop | `publish()` |

### E. Terminal Events
| Event | Publisher | Subscribers | Dispatch Mode |
|---|---|---|---|
| `TerminalSpawnedEvent` | Terminal Manager | TUI (Terminal Viewer) | `emit()` |
| `TerminalLogEvent` | Terminal Manager | TUI (Terminal Viewer) | `emit()` |
| `TerminalExitedEvent` | Terminal Manager | TUI (Terminal Viewer), Agent | `emit()` |

### Dispatch Mode Guidelines
- **Use `publish()`** when the publisher needs to guarantee that all subscribers have processed the event before continuing (state changes, task delegation, approval flows).
- **Use `emit()`** when the publisher should not be blocked by subscriber speed (streaming messages, terminal logs, status updates).

---

## 4. The BaseEvent Contract

```python
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid
import time

@dataclass
class BaseEvent:
    """
    The root class for all system events.
    All events are immutable dataclasses with an auto-generated ID and timestamp.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # The component that published this event (for debugging/tracing)

    @property
    def event_type(self) -> str:
        """
        Returns the event type identifier, derived from the class name.
        This is used by the Event Bus for topic-based routing.
        """
        return self.__class__.__name__
```

### Key Properties:
- **`event_id`**: UUID for tracing a specific event across the system.
- **`timestamp`**: Monotonic creation time for ordering and debugging.
- **`source`**: Human-readable origin (e.g., `"orchestrator"`, `"coder_agent"`, `"tui.input_bar"`). Used in logs, not for routing.
- **`event_type`**: Derived from the class name. This is the **routing key** — subscribers register against this string.

### Example Concrete Events:

```python
@dataclass
class UserRequestEvent(BaseEvent):
    text: str = ""
    injected_context: str = ""  # Populated by the @prefix pre-processor

@dataclass
class StateChangeEvent(BaseEvent):
    task_id: str = ""
    from_state: str = ""
    to_state: str = ""

@dataclass
class AgentMessageEvent(BaseEvent):
    agent_name: str = ""
    content: str = ""
    is_monologue: bool = False  # True = internal <thinking>, False = user-facing

@dataclass
class SystemErrorEvent(BaseEvent):
    error_message: str = ""
    original_event_type: str = ""  # Which event processing caused the failure
    subscriber_id: str = ""        # Which subscriber failed
```

---

## 5. The AbstractEventBus Interface

```python
from abc import ABC, abstractmethod
from typing import Callable, Awaitable

# Type alias for subscriber callbacks
EventCallback = Callable[[BaseEvent], Awaitable[None]]


class AbstractEventBus(ABC):
    """
    The Central Message Broker.
    Components publish events; subscribers receive them by event_type.
    """

    @abstractmethod
    async def publish(self, event: BaseEvent) -> None:
        """
        Synchronous dispatch.
        Awaits ALL registered callbacks for this event_type in priority order.
        The publisher is blocked until every subscriber has finished processing.

        Use for: StateChangeEvent, UserRequestEvent, TaskDelegatedEvent,
                 UserApprovalRequestEvent — any event where ordering matters.

        Error Handling: If a subscriber raises, the error is caught and a
                        SystemErrorEvent is emitted. Remaining subscribers
                        still execute.
        """
        pass

    @abstractmethod
    async def emit(self, event: BaseEvent) -> None:
        """
        Fire-and-forget dispatch.
        Schedules all registered callbacks as independent asyncio.Tasks
        and returns immediately. The publisher is NOT blocked.

        Use for: AgentMessageEvent, TerminalLogEvent, ToolExecutionStartEvent
                 — high-volume or non-critical events.

        Error Handling: Same as publish() — errors in background tasks are
                        caught and emitted as SystemErrorEvents.
        """
        pass

    @abstractmethod
    def subscribe(
        self,
        event_type: str,
        callback: EventCallback,
        priority: int = 0
    ) -> str:
        """
        Register a callback for a specific event type.

        Args:
            event_type: The event class name (e.g., "UserRequestEvent").
            callback:   An async function accepting a BaseEvent subclass.
            priority:   Lower number = processed first.
                        Recommended: 0 for StateManager, 10 for Orchestrator, 50 for TUI.

        Returns:
            A unique subscription_id string for later unsubscription.
        """
        pass

    @abstractmethod
    def unsubscribe(self, subscription_id: str) -> None:
        """
        Remove a subscription by its ID.
        Safe to call multiple times (idempotent).
        Must be called when a TUI widget is destroyed to prevent orphan callbacks.
        """
        pass

    @abstractmethod
    async def drain(self) -> None:
        """
        Graceful shutdown protocol:
        1. Sets the bus to DRAINING state (rejects new publish/emit calls).
        2. Waits for all in-flight background asyncio.Tasks (from emit()) to complete.
        3. Sets the bus to STOPPED state.

        Called by the Orchestrator when handling SystemShutdownEvent or Ctrl+C signal.
        """
        pass
```

---

## 6. Concrete Implementation: `AsyncEventBus`

```python
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class BusState(Enum):
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
    """
    Production implementation of the Event Bus.
    Uses topic-based routing with dual dispatch modes.
    """

    def __init__(self):
        # Registry: event_type -> list of subscriptions
        self._subscriptions: dict[str, list[_Subscription]] = defaultdict(list)
        
        # Track background tasks from emit() for graceful shutdown
        self._background_tasks: set[asyncio.Task] = set()
        
        # Bus lifecycle state
        self._state: BusState = BusState.RUNNING

    # ── Dispatch ──────────────────────────────────────────────────

    async def publish(self, event: BaseEvent) -> None:
        """Synchronous dispatch — awaits all callbacks in priority order."""
        if self._state != BusState.RUNNING:
            logger.warning(f"Bus is {self._state.name}; dropping publish({event.event_type})")
            return

        for sub in self._get_sorted_subscriptions(event.event_type):
            await self._safe_invoke(sub, event)

    async def emit(self, event: BaseEvent) -> None:
        """Fire-and-forget — schedules callbacks as background tasks."""
        if self._state != BusState.RUNNING:
            logger.warning(f"Bus is {self._state.name}; dropping emit({event.event_type})")
            return

        for sub in self._get_sorted_subscriptions(event.event_type):
            task = asyncio.create_task(
                self._safe_invoke(sub, event),
                name=f"event:{event.event_type}→{sub.id}"
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    # ── Subscription Management ───────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        callback: EventCallback,
        priority: int = 0
    ) -> str:
        sub_id = str(uuid.uuid4())
        self._subscriptions[event_type].append(
            _Subscription(
                id=sub_id,
                event_type=event_type,
                callback=callback,
                priority=priority
            )
        )
        logger.debug(f"Subscribed {sub_id} to {event_type} (priority={priority})")
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        for event_type, subs in self._subscriptions.items():
            before = len(subs)
            self._subscriptions[event_type] = [
                s for s in subs if s.id != subscription_id
            ]
            if len(self._subscriptions[event_type]) < before:
                logger.debug(f"Unsubscribed {subscription_id} from {event_type}")
                return  # IDs are unique; stop after first match

    # ── Lifecycle ─────────────────────────────────────────────────

    async def drain(self) -> None:
        """Graceful shutdown: stop accepting, wait for in-flight tasks."""
        self._state = BusState.DRAINING
        logger.info(f"Event Bus draining... {len(self._background_tasks)} tasks pending")

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        self._state = BusState.STOPPED
        logger.info("Event Bus stopped.")

    # ── Internal Helpers ──────────────────────────────────────────

    def _get_sorted_subscriptions(self, event_type: str) -> list[_Subscription]:
        """Returns subscriptions sorted by priority (ascending = higher priority first)."""
        return sorted(
            self._subscriptions.get(event_type, []),
            key=lambda s: s.priority
        )

    async def _safe_invoke(self, sub: _Subscription, event: BaseEvent) -> None:
        """
        Invoke a single subscriber callback with full error isolation.
        On failure: log the error AND emit a SystemErrorEvent.
        """
        try:
            await sub.callback(event)
        except Exception as e:
            logger.error(
                f"Subscriber {sub.id} failed on {event.event_type}: {e}",
                exc_info=True
            )
            # Emit a SystemErrorEvent so the TUI error panel can display it.
            # Guard against infinite recursion: don't re-emit if we ARE the error event.
            if event.event_type != "SystemErrorEvent":
                error_event = SystemErrorEvent(
                    source="event_bus",
                    error_message=str(e),
                    original_event_type=event.event_type,
                    subscriber_id=sub.id
                )
                # Use emit() (fire-and-forget) to avoid blocking the current dispatch
                await self.emit(error_event)
```

---

## 7. Usage Patterns

### A. Component Initialization (Startup Wiring)

```python
# In the application bootstrap / main.py
bus = AsyncEventBus()

# Core systems subscribe first (low priority number = high priority)
state_manager = StateManager(bus)
bus.subscribe("StateChangeEvent", state_manager.on_state_change, priority=0)
bus.subscribe("UserRequestEvent", state_manager.on_user_request, priority=0)

# Orchestrator subscribes second
orchestrator = Orchestrator(bus, state_manager)
bus.subscribe("UserRequestEvent", orchestrator.on_user_request, priority=10)
bus.subscribe("TaskResultEvent", orchestrator.on_task_result, priority=10)

# TUI subscribes last (highest priority number = processed after core systems)
tui = AgentCLIApp(bus)
bus.subscribe("AgentMessageEvent", tui.on_agent_message, priority=50)
bus.subscribe("StateChangeEvent", tui.on_state_change, priority=50)
bus.subscribe("SystemErrorEvent", tui.on_system_error, priority=50)
```

### B. Publishing from an Agent

```python
class CoderAgent(BaseAgent):
    async def handle_task(self, task_event: TaskDelegatedEvent) -> None:
        while True:
            # Stream thinking to TUI (fire-and-forget, non-blocking)
            await self.event_bus.emit(AgentMessageEvent(
                source="coder_agent",
                agent_name="coder",
                content="Analyzing the codebase...",
                is_monologue=True
            ))

            # ... LLM call, tool execution ...

            # Signal completion (synchronous — Orchestrator must receive this)
            await self.event_bus.publish(TaskResultEvent(
                source="coder_agent",
                task_id=task_event.task_id,
                result="Refactoring complete."
            ))
            break
```

### C. Graceful Shutdown

```python
import signal

def setup_shutdown(bus: AsyncEventBus):
    async def handle_shutdown():
        await bus.publish(SystemShutdownEvent(source="signal_handler"))
        await bus.drain()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(handle_shutdown()))
```

---

## 8. Priority Guidelines

To ensure deterministic processing order, components follow these priority bands:

| Priority Band | Components | Rationale |
|---|---|---|
| **0–9** (Critical) | State Manager | Must validate/update state before anyone reads it |
| **10–19** (Core Logic) | Orchestrator, Planner | Reacts to state transitions and delegates work |
| **20–39** (Services) | Memory Manager, Terminal Manager | Infrastructure services that support agents |
| **40–59** (Presentation) | TUI Widgets | Renders state that is already finalized |
| **60–79** (Utilities) | Logger, Cost Tracker, Analytics | Passive observers that never affect system state |

---

## 9. Testing Strategy

The Event Bus is trivially testable because it is entirely in-process:

```python
import pytest

@pytest.mark.asyncio
async def test_publish_calls_subscribers_in_priority_order():
    bus = AsyncEventBus()
    call_order = []

    async def low_priority(event): call_order.append("low")
    async def high_priority(event): call_order.append("high")

    bus.subscribe("TestEvent", low_priority, priority=50)
    bus.subscribe("TestEvent", high_priority, priority=0)

    await bus.publish(BaseEvent())  # event_type = "BaseEvent" — use a test subclass
    assert call_order == ["high", "low"]

@pytest.mark.asyncio
async def test_subscriber_error_does_not_crash_publisher():
    bus = AsyncEventBus()
    results = []

    async def failing_sub(event): raise ValueError("boom")
    async def healthy_sub(event): results.append("ok")

    bus.subscribe("TestEvent", failing_sub, priority=0)
    bus.subscribe("TestEvent", healthy_sub, priority=10)

    await bus.publish(BaseEvent())  # Should NOT raise
    assert results == ["ok"]

@pytest.mark.asyncio
async def test_drain_waits_for_background_tasks():
    bus = AsyncEventBus()
    completed = []

    async def slow_sub(event):
        await asyncio.sleep(0.1)
        completed.append(True)

    bus.subscribe("TestEvent", slow_sub)
    await bus.emit(BaseEvent())

    assert completed == []       # Not yet finished
    await bus.drain()
    assert completed == [True]   # drain() waited
```
