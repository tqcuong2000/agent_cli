"""
Unit tests for the State Manager (Sub-Phase 1.2.5).

Tests cover:
- Task creation and correct initial state (PENDING)
- Valid FSM transitions
- Invalid transitions raising InvalidTransitionError
- Concurrent transition safety (race conditions)
- Parent-child hierarchy and cancellation cascading
- Automatic StateChangeEvent publishing to the Event Bus
"""

import asyncio

import pytest

from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import StateChangeEvent
from agent_cli.core.state.state_manager import TaskStateManager
from agent_cli.core.state.state_models import InvalidTransitionError, TaskRecord, TaskState


@pytest.mark.asyncio
async def test_create_task_initializes_pending():
    """Task creation sets PENDING state and tracks history."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    
    assert task.state == TaskState.PENDING
    assert task.description == "Test task"
    assert len(task.history) == 1
    assert task.history[0]["to"] == "PENDING"
    assert not task.is_terminal


@pytest.mark.asyncio
async def test_valid_transition():
    """A valid transition succeeds and updates history."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    assert task.state == TaskState.PENDING
    
    updated = await sm.transition(task.task_id, TaskState.ROUTING)
    
    assert updated.state == TaskState.ROUTING
    assert len(updated.history) == 2
    assert updated.history[-1]["from"] == "PENDING"
    assert updated.history[-1]["to"] == "ROUTING"


@pytest.mark.asyncio
async def test_invalid_transition_raises():
    """Attempting an illegal transition raises InvalidTransitionError."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    
    # PENDING -> SUCCESS is illegal
    with pytest.raises(InvalidTransitionError) as exc_info:
        await sm.transition(task.task_id, TaskState.SUCCESS)
        
    assert exc_info.value.from_state == TaskState.PENDING
    assert exc_info.value.to_state == TaskState.SUCCESS
    
    # State should remain unchanged
    assert sm.get_task(task.task_id).state == TaskState.PENDING


@pytest.mark.asyncio
async def test_terminal_state_rejects_all_transitions():
    """Once a task is terminal, it cannot transition further."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Test task")
    await sm.transition(task.task_id, TaskState.ROUTING)
    await sm.transition(task.task_id, TaskState.FAILED, error="LLM timeout")
    
    assert sm.get_task(task.task_id).is_terminal
    
    for state in TaskState:
        with pytest.raises(InvalidTransitionError):
            await sm.transition(task.task_id, state)


@pytest.mark.asyncio
async def test_concurrent_transitions_are_safe():
    """Tests the per-task lock. Simultaneous attempts should strictly serialize,
    with the later one often being rejected as an invalid transition 
    if the first transition makes the task terminal.
    """
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    task = await sm.create_task("Race test")
    await sm.transition(task.task_id, TaskState.ROUTING)
    await sm.transition(task.task_id, TaskState.WORKING)
    
    # Fire both SUCCESS and CANCELLED concurrently
    results = await asyncio.gather(
        sm.transition(task.task_id, TaskState.SUCCESS, result="Done"),
        sm.transition(task.task_id, TaskState.CANCELLED),
        return_exceptions=True
    )
    
    errors = [r for r in results if isinstance(r, InvalidTransitionError)]
    successes = [r for r in results if isinstance(r, TaskRecord)]
    
    # Exactly one should have succeeded, because the first to acquire the lock
    # will make it terminal, and the second will raise InvalidTransitionError.
    assert len(errors) == 1
    assert len(successes) == 1
    assert sm.get_task(task.task_id).is_terminal


@pytest.mark.asyncio
async def test_transition_auto_publishes_event():
    """State manager should automatically publish StateChangeEvent to the bus."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    published_events = []
    
    async def capture(event): 
        published_events.append(event)
        
    bus.subscribe("StateChangeEvent", capture)
    
    task = await sm.create_task("Publish test")
    await sm.transition(task.task_id, TaskState.ROUTING)
    
    assert len(published_events) == 2
    
    event1 = published_events[0]
    assert event1.event_type == "StateChangeEvent"
    assert event1.to_state == "PENDING"
    assert event1.from_state == ""
    
    event2 = published_events[1]
    assert event2.to_state == "ROUTING"
    assert event2.from_state == "PENDING"


@pytest.mark.asyncio
async def test_parent_child_linking_and_queries():
    """Child tasks should be properly linked and retrievable."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    parent = await sm.create_task("Parent")
    child1 = await sm.create_task("Child 1", parent_id=parent.task_id)
    child2 = await sm.create_task("Child 2", parent_id=parent.task_id)
    
    assert parent.task_id in [t.task_id for t in sm.get_active_tasks()]
    
    children = sm.get_children(parent.task_id)
    assert len(children) == 2
    assert child1.task_id in [c.task_id for c in children]
    assert child2.task_id in [c.task_id for c in children]


@pytest.mark.asyncio
async def test_cancel_task_tree_cascades():
    """Canceling a parent should cascade cancellation to all active descendants."""
    bus = AsyncEventBus()
    sm = TaskStateManager(event_bus=bus)
    
    parent = await sm.create_task("Parent")
    child1 = await sm.create_task("Child 1", parent_id=parent.task_id)
    child2 = await sm.create_task("Child 2", parent_id=parent.task_id)
    grandchild = await sm.create_task("Grandchild", parent_id=child1.task_id)
    
    await sm.transition(parent.task_id, TaskState.ROUTING)
    await sm.transition(child1.task_id, TaskState.ROUTING)
    await sm.transition(child1.task_id, TaskState.WORKING)
    
    # Grandchild transitions to SUCCESS (terminal) - shouldn't be touched by cancel
    await sm.transition(grandchild.task_id, TaskState.ROUTING)
    await sm.transition(grandchild.task_id, TaskState.WORKING)
    await sm.transition(grandchild.task_id, TaskState.SUCCESS)
    
    await sm.cancel_task_tree(parent.task_id)
    
    # Parent and active children should be Cancelled
    assert sm.get_task(parent.task_id).state == TaskState.CANCELLED
    assert sm.get_task(child1.task_id).state == TaskState.CANCELLED
    assert sm.get_task(child2.task_id).state == TaskState.CANCELLED
    
    # Grandchild was already terminal, shouldn't be cancelled (should remain SUCCESS)
    assert sm.get_task(grandchild.task_id).state == TaskState.SUCCESS
