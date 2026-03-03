# State Management Architecture (The Task Lifecycle FSM)

## Overview
The State Manager is the **single source of truth** for every task's current phase in the system. It enforces a strict Finite State Machine (FSM), validates that all transitions are legal, prevents race conditions via per-task locking, and automatically publishes `StateChangeEvent` to the Event Bus on every transition.

No component in the system may change a task's state outside of the State Manager. This guarantee is what keeps the TUI, Orchestrator, and Agents in perfect sync.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Failed = Terminal** | `FAILED` is final; retries create a new task | Clean audit trail per attempt. Fresh Working Memory on retry (aligns with Task Planning). |
| **Task Hierarchy** | Parent-Child tree | TUI can render an ExecutionPlan progress checklist. Orchestrator queries child states to determine parent completion. |
| **Auto-Publish on Transition** | `transition()` validates + updates + publishes `StateChangeEvent` | Impossible to change state without notifying the system. Single call does everything. |
| **Concurrency Guard** | `asyncio.Lock` per task | Prevents race conditions (e.g., simultaneous `SUCCESS` and `CANCELLED` attempts). Loser sees a terminal state and is rejected gracefully. |

---

## 2. The Task State Enum

```python
from enum import Enum, auto

class TaskState(Enum):
    """All possible states in the task lifecycle."""
    PENDING         = auto()  # Registered but not started
    ROUTING         = auto()  # Orchestrator analyzing the prompt
    WORKING         = auto()  # Agent actively in its ReAct loop
    AWAITING_INPUT  = auto()  # Paused — waiting for user approval or clarification
    SUCCESS         = auto()  # Terminal — completed successfully
    FAILED          = auto()  # Terminal — unrecoverable error or max iterations
    CANCELLED       = auto()  # Terminal — user explicitly cancelled
```

### Terminal vs. Active States

```python
TERMINAL_STATES = {TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED}
ACTIVE_STATES   = {TaskState.PENDING, TaskState.ROUTING, TaskState.WORKING, TaskState.AWAITING_INPUT}
```

---

## 3. The Formal Transition Table

Only the transitions listed below are legal. Any other transition attempt is rejected with an `InvalidTransitionError`.

```
                     ┌──────────┐
            ┌───────→│ CANCELLED│
            │        └──────────┘
            │              ▲  ▲
            │              │  │
       ┌────┴───┐    ┌─────┴──┴──┐        ┌─────────┐
       │PENDING │───→│  ROUTING  │───────→│ FAILED  │
       └────────┘    └─────┬─────┘        └─────────┘
                           │                    ▲
                           ▼                    │
                     ┌──────────┐               │
               ┌────→│ WORKING  │───────────────┤
               │     └────┬──┬──┘               │
               │          │  │                  │
               │          │  └────────────┐     │
               │          ▼               ▼     │
               │   ┌──────────────┐  ┌─────────┐
               └───│AWAITING_INPUT│  │ SUCCESS │
                   └──────────────┘  └─────────┘
```

### Transition Rules

| # | From | To | Trigger | Who Initiates |
|---|---|---|---|---|
| T1 | `PENDING` | `ROUTING` | Orchestrator picks up the task | Orchestrator |
| T2 | `PENDING` | `CANCELLED` | User cancels before processing | TUI / Command System |
| T3 | `ROUTING` | `WORKING` | Orchestrator delegates to an Agent | Orchestrator |
| T4 | `ROUTING` | `FAILED` | No suitable agent found, routing error | Orchestrator |
| T5 | `ROUTING` | `CANCELLED` | User cancels during routing | TUI / Command System |
| T6 | `WORKING` | `AWAITING_INPUT` | Agent needs user approval or clarification | Agent / Tool Executor |
| T7 | `WORKING` | `SUCCESS` | Agent completed the task | Agent |
| T8 | `WORKING` | `FAILED` | Max iterations hit, LLM API error, unrecoverable tool failure | Agent / Orchestrator |
| T9 | `WORKING` | `CANCELLED` | User cancels during execution | TUI / Command System |
| T10 | `AWAITING_INPUT` | `WORKING` | User responds (approves or provides clarification) | TUI |
| T11 | `AWAITING_INPUT` | `CANCELLED` | User cancels instead of responding | TUI / Command System |

### Encoded in Python

```python
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING:        {TaskState.ROUTING, TaskState.CANCELLED},
    TaskState.ROUTING:        {TaskState.WORKING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.WORKING:        {TaskState.AWAITING_INPUT, TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.AWAITING_INPUT: {TaskState.WORKING, TaskState.CANCELLED},
    # Terminal states — no outgoing transitions
    TaskState.SUCCESS:        set(),
    TaskState.FAILED:         set(),
    TaskState.CANCELLED:      set(),
}
```

---

## 4. The Task Model (Parent-Child Hierarchy)

Tasks are not flat. An `ExecutionPlan` (from `03_task_planning.md`) creates a **parent task** that owns multiple **child sub-tasks**. The State Manager tracks them all.

```python
from dataclasses import dataclass, field
from typing import Optional, List
import time
import uuid


@dataclass
class TaskRecord:
    """
    A single tracked task in the system.
    Can be a top-level task or a child of an ExecutionPlan.
    """
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: TaskState = TaskState.PENDING
    
    # Descriptive metadata
    description: str = ""
    assigned_agent: str = ""         # e.g., "coder_agent", "researcher_agent"
    
    # Parent-Child relationship
    parent_id: Optional[str] = None  # None = top-level task
    children_ids: List[str] = field(default_factory=list)
    
    # Timestamps for auditing
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    # Transition history (append-only log for debugging)
    history: List[dict] = field(default_factory=list)
    
    # Result data (populated on terminal state)
    result: Optional[str] = None     # Final output on SUCCESS
    error: Optional[str] = None      # Error message on FAILED

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
```

### Parent-Child State Rules

The parent task's state is derived from its children:

| Children States | Parent Auto-Transition |
|---|---|
| All children `SUCCESS` | Parent → `SUCCESS` |
| Any child `FAILED` (and no retry) | Parent → `FAILED` |
| Any child `CANCELLED` | Parent → `CANCELLED` |
| Mix of `SUCCESS` + `PENDING`/`WORKING` | Parent stays `WORKING` |

The Orchestrator drives these parent transitions after each child completes, by inspecting the children and calling `transition()` on the parent when appropriate.

---

## 5. The AbstractStateManager Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, List


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""
    def __init__(self, task_id: str, from_state: TaskState, to_state: TaskState):
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition for task '{task_id}': "
            f"{from_state.name} → {to_state.name}"
        )


class AbstractStateManager(ABC):
    """
    The single source of truth for task lifecycle state.
    All state mutations go through this interface.
    """

    @abstractmethod
    async def create_task(
        self,
        description: str,
        parent_id: Optional[str] = None,
        assigned_agent: str = ""
    ) -> TaskRecord:
        """
        Register a new task in PENDING state.
        If parent_id is provided, the task is linked as a child.
        Publishes a StateChangeEvent(from_state=None, to_state=PENDING).
        """
        pass

    @abstractmethod
    async def transition(
        self,
        task_id: str,
        to_state: TaskState,
        result: Optional[str] = None,
        error: Optional[str] = None
    ) -> TaskRecord:
        """
        Attempt to move a task to a new state.
        
        Flow:
        1. Acquire the task's asyncio.Lock.
        2. Validate: is `current_state → to_state` in VALID_TRANSITIONS?
        3. If invalid: raise InvalidTransitionError (does NOT publish).
        4. If valid: update state, record timestamp and history entry.
        5. Publish StateChangeEvent to the Event Bus.
        6. Release lock and return the updated TaskRecord.
        
        Args:
            result: Populated when transitioning to SUCCESS.
            error:  Populated when transitioning to FAILED.
        """
        pass

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Retrieve a task by ID. Returns None if not found."""
        pass

    @abstractmethod
    def get_children(self, parent_id: str) -> List[TaskRecord]:
        """Retrieve all child tasks of an ExecutionPlan parent."""
        pass

    @abstractmethod
    def get_active_tasks(self) -> List[TaskRecord]:
        """Return all tasks in non-terminal states (for TUI status display)."""
        pass
```

---

## 6. Concrete Implementation: `TaskStateManager`

```python
import asyncio
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


class TaskStateManager(AbstractStateManager):
    """
    In-memory state manager with per-task locking 
    and auto-publish to Event Bus.
    """

    def __init__(self, event_bus: AbstractEventBus):
        self.event_bus = event_bus
        
        # Task registry: task_id -> TaskRecord
        self._tasks: dict[str, TaskRecord] = {}
        
        # Per-task locks for concurrent transition safety
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Task Creation ─────────────────────────────────────────────

    async def create_task(
        self,
        description: str,
        parent_id: Optional[str] = None,
        assigned_agent: str = ""
    ) -> TaskRecord:
        task = TaskRecord(
            description=description,
            parent_id=parent_id,
            assigned_agent=assigned_agent
        )
        task.history.append({
            "from": None,
            "to": TaskState.PENDING.name,
            "timestamp": task.created_at
        })

        self._tasks[task.task_id] = task
        self._locks[task.task_id] = asyncio.Lock()

        # Link to parent if applicable
        if parent_id and parent_id in self._tasks:
            self._tasks[parent_id].children_ids.append(task.task_id)

        # Publish creation event
        await self.event_bus.publish(StateChangeEvent(
            source="state_manager",
            task_id=task.task_id,
            from_state="",
            to_state=TaskState.PENDING.name
        ))

        logger.info(f"Task created: {task.task_id} ({description[:50]})")
        return task

    # ── State Transitions ─────────────────────────────────────────

    async def transition(
        self,
        task_id: str,
        to_state: TaskState,
        result: Optional[str] = None,
        error: Optional[str] = None
    ) -> TaskRecord:
        if task_id not in self._tasks:
            raise KeyError(f"Task '{task_id}' not found in state manager.")

        async with self._locks[task_id]:
            task = self._tasks[task_id]
            from_state = task.state

            # ── Validate transition ──
            if to_state not in VALID_TRANSITIONS.get(from_state, set()):
                raise InvalidTransitionError(task_id, from_state, to_state)

            # ── Commit transition ──
            task.state = to_state
            task.updated_at = time.time()
            task.history.append({
                "from": from_state.name,
                "to": to_state.name,
                "timestamp": task.updated_at
            })

            # Attach terminal data
            if to_state == TaskState.SUCCESS and result is not None:
                task.result = result
            if to_state == TaskState.FAILED and error is not None:
                task.error = error

            logger.info(f"Task {task_id}: {from_state.name} → {to_state.name}")

        # ── Auto-publish (outside the lock to avoid deadlocks) ──
        await self.event_bus.publish(StateChangeEvent(
            source="state_manager",
            task_id=task_id,
            from_state=from_state.name,
            to_state=to_state.name
        ))

        return task

    # ── Queries ───────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def get_children(self, parent_id: str) -> List[TaskRecord]:
        parent = self._tasks.get(parent_id)
        if not parent:
            return []
        return [self._tasks[cid] for cid in parent.children_ids if cid in self._tasks]

    def get_active_tasks(self) -> List[TaskRecord]:
        return [t for t in self._tasks.values() if not t.is_terminal]
```

---

## 7. Integration with the Event Bus

The State Manager subscribes to the Event Bus at **priority 0** (highest) so that state is always updated before any other component reads it:

```python
# During application bootstrap
bus = AsyncEventBus()
state_manager = TaskStateManager(event_bus=bus)

# The State Manager does NOT subscribe to its OWN StateChangeEvent
# (that would cause infinite recursion). It only subscribes to 
# external triggers that require state inspection:
bus.subscribe("SystemShutdownEvent", state_manager.on_shutdown, priority=0)
```

### Who Calls `transition()`?

The State Manager does **not** transition itself based on events. Instead, the **component responsible for the phase** calls `transition()` directly:

| Transition | Called By | Code Location |
|---|---|---|
| `PENDING → ROUTING` | Orchestrator | `orchestrator.on_user_request()` |
| `ROUTING → WORKING` | Orchestrator | `orchestrator.delegate_task()` |
| `ROUTING → FAILED` | Orchestrator | `orchestrator.delegate_task()` (no agent found) |
| `WORKING → AWAITING_INPUT` | Tool Executor | `tool_executor.check_safety()` |
| `WORKING → SUCCESS` | Agent | `agent.handle_task()` (loop exits with final answer) |
| `WORKING → FAILED` | Agent / Orchestrator | `agent.handle_task()` (max iterations) |
| `AWAITING_INPUT → WORKING` | TUI / Interaction Handler | `interaction_handler.on_user_response()` |
| `* → CANCELLED` | TUI / Command System | `/cancel` command or `Ctrl+C` handler |

---

## 8. Retry Strategy (FAILED is Terminal)

When a task fails, retries are handled by **creating a new task**, not by re-transitioning the failed one:

```python
# In the Orchestrator, after receiving a TaskResultEvent with failure:
async def handle_task_failure(self, failed_task: TaskRecord):
    retry_count = self._get_retry_count(failed_task)
    
    if retry_count < self.config.max_retries:
        # Create a fresh task with clean Working Memory
        new_task = await self.state_manager.create_task(
            description=f"[Retry #{retry_count + 1}] {failed_task.description}",
            parent_id=failed_task.parent_id,
            assigned_agent=failed_task.assigned_agent
        )
        # Orchestrator will pick up the new PENDING task naturally
    else:
        # Give up — propagate failure to parent if exists
        if failed_task.parent_id:
            await self.state_manager.transition(
                failed_task.parent_id,
                TaskState.FAILED,
                error=f"Child task failed after {retry_count} retries."
            )
```

### Benefits of this approach:
1. **Audit trail**: Every attempt has its own `TaskRecord` with history.
2. **Fresh memory**: Retried task starts from a clean Working Memory (no polluted context from the failed attempt).
3. **No FSM loops**: Terminal states are truly terminal — simpler to reason about.

---

## 9. Cancellation Cascade

When a user cancels a parent task, all active children must also be cancelled:

```python
async def cancel_task_tree(self, task_id: str) -> None:
    """Cancel a task and all its active descendants."""
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
        pass  # Already in a terminal state (race condition — safe to ignore)
```

---

## 10. TUI Integration (Reactive State Display)

The TUI subscribes to `StateChangeEvent` and reactively updates its widgets:

```python
class StatusBar(Widget):
    """Displays the current task state in the TUI header."""

    def __init__(self, event_bus: AbstractEventBus):
        super().__init__()
        event_bus.subscribe("StateChangeEvent", self.on_state_change, priority=50)

    async def on_state_change(self, event: StateChangeEvent):
        state_label = event.to_state
        state_colors = {
            "PENDING": "dim white",
            "ROUTING": "yellow",
            "WORKING": "bold cyan",
            "AWAITING_INPUT": "bold magenta",
            "SUCCESS": "bold green",
            "FAILED": "bold red",
            "CANCELLED": "dim red",
        }
        color = state_colors.get(state_label, "white")
        self.update(f"[{color}]● {state_label}[/]")
```

### ExecutionPlan Progress (Parent-Child Display)

```python
class PlanProgressWidget(Widget):
    """Renders the checklist of subtasks from an ExecutionPlan."""

    async def on_state_change(self, event: StateChangeEvent):
        task = self.state_manager.get_task(event.task_id)
        if task and task.parent_id:
            # This is a sub-task — refresh the plan checklist
            siblings = self.state_manager.get_children(task.parent_id)
            self.render_checklist(siblings)
    
    def render_checklist(self, tasks: List[TaskRecord]):
        icons = {
            TaskState.PENDING: "○",
            TaskState.WORKING: "◉",
            TaskState.SUCCESS: "✓",
            TaskState.FAILED: "✗",
            TaskState.CANCELLED: "—",
        }
        for t in tasks:
            icon = icons.get(t.state, "?")
            # Renders: ✓ t_1: Remove cookie-based logic
            #          ◉ t_2: Implement JWT middleware  (current)
            #          ○ t_3: Write unit tests
            self.log(f"{icon} {t.task_id}: {t.description}")
```

---

## 11. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_valid_transition():
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    assert task.state == TaskState.PENDING
    
    updated = await sm.transition(task.task_id, TaskState.ROUTING)
    assert updated.state == TaskState.ROUTING
    assert len(updated.history) == 2  # PENDING creation + ROUTING transition

@pytest.mark.asyncio
async def test_invalid_transition_raises():
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    
    with pytest.raises(InvalidTransitionError):
        await sm.transition(task.task_id, TaskState.SUCCESS)  # PENDING → SUCCESS is illegal

@pytest.mark.asyncio
async def test_terminal_state_rejects_all_transitions():
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    await sm.transition(task.task_id, TaskState.ROUTING)
    await sm.transition(task.task_id, TaskState.FAILED, error="LLM timeout")
    
    # FAILED is terminal — all transitions should be rejected
    for state in TaskState:
        with pytest.raises(InvalidTransitionError):
            await sm.transition(task.task_id, state)

@pytest.mark.asyncio
async def test_concurrent_transitions_are_safe():
    """Simulates user cancel racing with agent success."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Race condition test")
    await sm.transition(task.task_id, TaskState.ROUTING)
    await sm.transition(task.task_id, TaskState.WORKING)
    
    # Fire both transitions concurrently
    results = await asyncio.gather(
        sm.transition(task.task_id, TaskState.SUCCESS),
        sm.transition(task.task_id, TaskState.CANCELLED),
        return_exceptions=True
    )
    
    # Exactly one should succeed, one should raise InvalidTransitionError
    errors = [r for r in results if isinstance(r, InvalidTransitionError)]
    successes = [r for r in results if isinstance(r, TaskRecord)]
    
    assert len(errors) == 1
    assert len(successes) == 1
    assert sm.get_task(task.task_id).is_terminal

@pytest.mark.asyncio
async def test_transition_auto_publishes_event():
    """Verify that transition() publishes StateChangeEvent to the bus."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    published_events = []
    
    async def capture(event): published_events.append(event)
    bus.subscribe("StateChangeEvent", capture)
    
    task = await sm.create_task("Publish test")
    await sm.transition(task.task_id, TaskState.ROUTING)
    
    # Should have 2 events: creation (→PENDING) + transition (→ROUTING)
    assert len(published_events) == 2
    assert published_events[1].to_state == "ROUTING"
```
