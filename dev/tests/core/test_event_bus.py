"""
Unit tests for the Event Bus (Sub-Phase 1.1).

Tests cover:
- publish() calls subscribers in priority order
- emit() schedules callbacks as fire-and-forget background tasks
- Subscriber errors do not crash the publisher
- Subscriber errors emit SystemErrorEvent
- unsubscribe() removes specific subscriptions
- drain() waits for background tasks to complete
- drain() rejects new events after draining
- Event type routing (only matching subscribers fire)
- Multiple subscribers for the same event type
"""

from __future__ import annotations

import asyncio

import pytest

from agent_cli.core.infra.events.event_bus import AsyncEventBus, BusState
from agent_cli.core.infra.events.events import (
    AgentMessageEvent,
    BaseEvent,
    StateChangeEvent,
    SystemErrorEvent,
    UserRequestEvent,
)


# ── Helpers ──────────────────────────────────────────────────────────


class _TestEvent(BaseEvent):
    """A simple event subclass for testing."""
    pass


# ── publish() Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_calls_subscribers_in_priority_order():
    """Subscribers should be invoked in ascending priority order."""
    bus = AsyncEventBus()
    call_order: list[str] = []

    async def low_priority(event: BaseEvent) -> None:
        call_order.append("low")

    async def high_priority(event: BaseEvent) -> None:
        call_order.append("high")

    async def mid_priority(event: BaseEvent) -> None:
        call_order.append("mid")

    bus.subscribe("_TestEvent", low_priority, priority=50)
    bus.subscribe("_TestEvent", high_priority, priority=0)
    bus.subscribe("_TestEvent", mid_priority, priority=10)

    await bus.publish(_TestEvent(source="test"))

    assert call_order == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_publish_only_routes_to_matching_event_type():
    """Publishing a UserRequestEvent should NOT trigger a StateChangeEvent subscriber."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def on_user_request(event: BaseEvent) -> None:
        results.append("user_request")

    async def on_state_change(event: BaseEvent) -> None:
        results.append("state_change")

    bus.subscribe("UserRequestEvent", on_user_request)
    bus.subscribe("StateChangeEvent", on_state_change)

    await bus.publish(UserRequestEvent(source="test", text="hello"))

    assert results == ["user_request"]


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_silent():
    """Publishing an event with no subscribers should not raise."""
    bus = AsyncEventBus()
    await bus.publish(_TestEvent(source="test"))  # Should not raise


@pytest.mark.asyncio
async def test_publish_multiple_subscribers_same_priority():
    """Multiple subscribers at the same priority should all fire."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def sub_a(event: BaseEvent) -> None:
        results.append("a")

    async def sub_b(event: BaseEvent) -> None:
        results.append("b")

    bus.subscribe("_TestEvent", sub_a, priority=10)
    bus.subscribe("_TestEvent", sub_b, priority=10)

    await bus.publish(_TestEvent(source="test"))

    assert len(results) == 2
    assert set(results) == {"a", "b"}


# ── Error Isolation Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_error_does_not_crash_publisher():
    """A failing subscriber must NOT prevent subsequent subscribers from running."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def failing_sub(event: BaseEvent) -> None:
        raise ValueError("boom")

    async def healthy_sub(event: BaseEvent) -> None:
        results.append("ok")

    bus.subscribe("_TestEvent", failing_sub, priority=0)
    bus.subscribe("_TestEvent", healthy_sub, priority=10)

    await bus.publish(_TestEvent(source="test"))  # Should NOT raise

    assert results == ["ok"]


@pytest.mark.asyncio
async def test_subscriber_error_emits_system_error_event():
    """When a subscriber fails, a SystemErrorEvent should be emitted."""
    bus = AsyncEventBus()
    error_events: list[SystemErrorEvent] = []

    async def failing_sub(event: BaseEvent) -> None:
        raise ValueError("test error")

    async def error_listener(event: BaseEvent) -> None:
        if isinstance(event, SystemErrorEvent):
            error_events.append(event)

    bus.subscribe("_TestEvent", failing_sub)
    bus.subscribe("SystemErrorEvent", error_listener)

    await bus.publish(_TestEvent(source="test"))

    # Give background tasks time to complete (SystemErrorEvent is emitted via emit())
    await asyncio.sleep(0.05)

    assert len(error_events) == 1
    assert "test error" in error_events[0].error_message
    assert error_events[0].original_event_type == "_TestEvent"


@pytest.mark.asyncio
async def test_system_error_event_does_not_recurse():
    """An error while processing SystemErrorEvent must NOT emit another SystemErrorEvent."""
    bus = AsyncEventBus()
    error_count = 0

    async def failing_error_handler(event: BaseEvent) -> None:
        nonlocal error_count
        error_count += 1
        raise RuntimeError("handler also fails")

    bus.subscribe("SystemErrorEvent", failing_error_handler)

    # Manually invoke _safe_invoke with a SystemErrorEvent
    from agent_cli.core.infra.events.event_bus import _Subscription

    sub = _Subscription(
        id="test-sub",
        event_type="_TestEvent",
        callback=lambda e: (_ for _ in ()).throw(RuntimeError("boom")),
        priority=0,
    )

    # This should not cause infinite recursion
    error_event = SystemErrorEvent(source="test", error_message="original")
    await bus._safe_invoke(sub, error_event)

    # The error handler for SystemErrorEvent was NOT called recursively
    assert error_count == 0


# ── emit() Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_schedules_background_tasks():
    """emit() should schedule callbacks without blocking the publisher."""
    bus = AsyncEventBus()
    completed: list[bool] = []

    async def slow_sub(event: BaseEvent) -> None:
        await asyncio.sleep(0.05)
        completed.append(True)

    bus.subscribe("_TestEvent", slow_sub)
    await bus.emit(_TestEvent(source="test"))

    # Should NOT be completed yet (fire-and-forget)
    assert completed == []

    # Wait for background task to finish
    await asyncio.sleep(0.1)
    assert completed == [True]


@pytest.mark.asyncio
async def test_emit_tracks_pending_tasks():
    """Background tasks from emit() should be tracked."""
    bus = AsyncEventBus()

    async def slow_sub(event: BaseEvent) -> None:
        await asyncio.sleep(0.1)

    bus.subscribe("_TestEvent", slow_sub)
    await bus.emit(_TestEvent(source="test"))

    assert bus.pending_task_count >= 1

    await asyncio.sleep(0.15)
    assert bus.pending_task_count == 0


# ── subscribe() / unsubscribe() Tests ───────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_returns_unique_id():
    """Each subscription should get a unique ID."""
    bus = AsyncEventBus()

    async def callback(event: BaseEvent) -> None:
        pass

    id1 = bus.subscribe("_TestEvent", callback)
    id2 = bus.subscribe("_TestEvent", callback)

    assert id1 != id2


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscription():
    """After unsubscribing, the callback should no longer be called."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def callback(event: BaseEvent) -> None:
        results.append("called")

    sub_id = bus.subscribe("_TestEvent", callback)
    assert bus.subscriber_count("_TestEvent") == 1

    bus.unsubscribe(sub_id)
    assert bus.subscriber_count("_TestEvent") == 0

    await bus.publish(_TestEvent(source="test"))
    assert results == []


@pytest.mark.asyncio
async def test_unsubscribe_idempotent():
    """Calling unsubscribe twice with the same ID should not raise."""
    bus = AsyncEventBus()

    async def callback(event: BaseEvent) -> None:
        pass

    sub_id = bus.subscribe("_TestEvent", callback)
    bus.unsubscribe(sub_id)
    bus.unsubscribe(sub_id)  # Should not raise


@pytest.mark.asyncio
async def test_unsubscribe_only_removes_target():
    """Unsubscribing one callback should not affect others."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def sub_a(event: BaseEvent) -> None:
        results.append("a")

    async def sub_b(event: BaseEvent) -> None:
        results.append("b")

    id_a = bus.subscribe("_TestEvent", sub_a)
    bus.subscribe("_TestEvent", sub_b)

    bus.unsubscribe(id_a)

    await bus.publish(_TestEvent(source="test"))
    assert results == ["b"]


# ── drain() Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_waits_for_background_tasks():
    """drain() should block until all background tasks complete."""
    bus = AsyncEventBus()
    completed: list[bool] = []

    async def slow_sub(event: BaseEvent) -> None:
        await asyncio.sleep(0.05)
        completed.append(True)

    bus.subscribe("_TestEvent", slow_sub)
    await bus.emit(_TestEvent(source="test"))

    assert completed == []
    await bus.drain()
    assert completed == [True]


@pytest.mark.asyncio
async def test_drain_transitions_to_stopped():
    """After drain(), bus state should be STOPPED."""
    bus = AsyncEventBus()

    assert bus.state == BusState.RUNNING
    await bus.drain()
    assert bus.state == BusState.STOPPED


@pytest.mark.asyncio
async def test_publish_rejected_after_drain():
    """Publishing after drain should be silently dropped."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def callback(event: BaseEvent) -> None:
        results.append("called")

    bus.subscribe("_TestEvent", callback)
    await bus.drain()

    await bus.publish(_TestEvent(source="test"))
    assert results == []


@pytest.mark.asyncio
async def test_emit_rejected_after_drain():
    """Emitting after drain should be silently dropped."""
    bus = AsyncEventBus()
    results: list[str] = []

    async def callback(event: BaseEvent) -> None:
        results.append("called")

    bus.subscribe("_TestEvent", callback)
    await bus.drain()

    await bus.emit(_TestEvent(source="test"))
    await asyncio.sleep(0.05)
    assert results == []


# ── BaseEvent Tests ──────────────────────────────────────────────────


def test_base_event_has_unique_id():
    """Each event instance should have a unique ID."""
    e1 = BaseEvent(source="test")
    e2 = BaseEvent(source="test")
    assert e1.event_id != e2.event_id


def test_base_event_has_timestamp():
    """Events should have an auto-generated timestamp."""
    event = BaseEvent(source="test")
    assert event.timestamp > 0


def test_event_type_matches_class_name():
    """event_type should return the concrete class name."""
    event = UserRequestEvent(source="test", text="hello")
    assert event.event_type == "UserRequestEvent"

    base = BaseEvent(source="test")
    assert base.event_type == "BaseEvent"


def test_event_subclass_inherits_base_fields():
    """All event subclasses should have event_id, timestamp, and source."""
    event = AgentMessageEvent(
        source="coder_agent",
        agent_name="coder",
        content="Analyzing...",
        is_monologue=True,
    )
    assert event.event_id
    assert event.timestamp > 0
    assert event.source == "coder_agent"
    assert event.agent_name == "coder"
    assert event.is_monologue is True
