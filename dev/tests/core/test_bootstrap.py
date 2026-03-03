"""
Integration tests for the DI Bootstrap (Sub-Phase 1.5).

These tests verify that all Phase 1 components wire together
correctly and that the full lifecycle (create → startup → use → shutdown)
works end-to-end.
"""

import asyncio
import os

import pytest

from agent_cli.core.bootstrap import AppContext, create_app
from agent_cli.core.config import AgentSettings
from agent_cli.core.models.config_models import ProtocolMode

os.environ["OPENAI_API_KEY"] = "mock_key_for_testing"
from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.events.event_bus import AsyncEventBus, BusState
from agent_cli.core.events.events import StateChangeEvent, UserRequestEvent
from agent_cli.core.state.state_models import TaskState
from agent_cli.data import DataRegistry
from agent_cli.providers.manager import ProviderManager
from agent_cli.workspace.sandbox import SandboxWorkspaceManager

# ── Factory Tests ─────────────────────────────────────────────────────


def test_create_app_returns_app_context():
    """create_app() should return a fully wired AppContext."""
    ctx = create_app()

    assert isinstance(ctx, AppContext)
    assert isinstance(ctx.data_registry, DataRegistry)
    assert isinstance(ctx.settings, AgentSettings)
    assert isinstance(ctx.event_bus, AsyncEventBus)
    assert ctx.state_manager is not None
    assert isinstance(ctx.providers, ProviderManager)
    assert ctx.session_manager is not None
    assert ctx.file_indexer is not None
    assert ctx.agent_registry is not None
    assert ctx.session_agents is not None
    assert ctx.orchestrator is not None
    assert ctx.orchestrator.active_agent_name == "default"
    assert ctx.is_running is False  # Not started yet


def test_create_app_accepts_custom_settings():
    """Custom settings passed to create_app() should be used."""
    custom = AgentSettings(default_model="gpt-4o", log_level="DEBUG")
    ctx = create_app(settings=custom)

    assert ctx.settings.default_model == "gpt-4o"
    assert ctx.settings.log_level == "DEBUG"


def test_create_app_wires_protocol_mode_into_schema_validator():
    custom = AgentSettings(core={"protocol_mode": "json_only"})
    ctx = create_app(settings=custom)
    assert ctx.schema_validator.protocol_mode == ProtocolMode.JSON_ONLY


def test_create_app_applies_built_in_agent_overrides_from_settings():
    custom = AgentSettings(
        default_model="gpt-4o-mini",
        agents={
            "coder": {
                "model": "gpt-5-mini",
                "max_iterations": 220,
            }
        },
    )
    ctx = create_app(settings=custom)
    assert ctx.agent_registry is not None

    coder = ctx.agent_registry.get("coder")
    assert coder is not None
    assert coder.config.model == "gpt-5-mini"
    assert coder.config.max_iterations_override == 220


def test_create_app_registers_ask_user_tool():
    """Default tool registry should include ask_user for clarification flow."""
    ctx = create_app()
    assert "ask_user" in ctx.tool_registry.get_all_names()


def test_create_app_wires_configurable_workspace_policy(tmp_path):
    settings = AgentSettings(
        workspace_deny_patterns=["*.key"],
        workspace_allow_overrides=["allowed.key"],
    )
    ctx = create_app(settings=settings, root_folder=tmp_path)
    manager = ctx.workspace_manager
    assert isinstance(manager, SandboxWorkspaceManager)

    denied = tmp_path / "secret.key"
    denied.write_text("secret")
    allowed = tmp_path / "allowed.key"
    allowed.write_text("ok")

    with pytest.raises(ToolExecutionError):
        manager.resolve_path("secret.key", must_exist=True)
    assert manager.resolve_path("allowed.key", must_exist=True) == allowed.resolve()


# ── Lifecycle Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_and_shutdown():
    """Full lifecycle: create → startup → shutdown."""
    ctx = create_app()

    assert ctx.is_running is False

    await ctx.startup()
    assert ctx.is_running is True
    assert ctx.session_manager is not None
    assert ctx.session_manager.get_active() is None
    assert ctx.file_indexer is not None

    await ctx.shutdown()
    assert ctx.is_running is False
    assert ctx.event_bus.state == BusState.STOPPED


@pytest.mark.asyncio
async def test_startup_does_not_create_session():
    """Startup should not create sessions until first routed user request."""
    ctx = create_app()
    await ctx.startup()
    assert ctx.session_manager is not None
    assert ctx.session_manager.get_active() is None
    await ctx.shutdown()


@pytest.mark.asyncio
async def test_startup_is_idempotent():
    """Calling startup() twice should not raise or re-initialize."""
    ctx = create_app()

    await ctx.startup()
    await ctx.startup()  # Should be a no-op

    assert ctx.is_running is True

    await ctx.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent():
    """Calling shutdown() without startup should be safe."""
    ctx = create_app()
    await ctx.shutdown()  # Should be a no-op
    assert ctx.is_running is False


# ── End-to-End Integration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_task_lifecycle():
    """Integration: create a task, transition through the FSM, and verify
    events are published on the bus.
    """
    ctx = create_app()
    await ctx.startup()

    # Subscribe to state changes
    events_received: list[StateChangeEvent] = []

    async def on_state_change(event):
        events_received.append(event)

    ctx.event_bus.subscribe("StateChangeEvent", on_state_change)

    # Create a task
    task = await ctx.state_manager.create_task(
        description="Write unit tests",
        assigned_agent="coder_agent",
    )
    assert task.state == TaskState.PENDING

    # Transition: PENDING → ROUTING → WORKING → SUCCESS
    await ctx.state_manager.transition(task.task_id, TaskState.ROUTING)
    await ctx.state_manager.transition(task.task_id, TaskState.WORKING)
    await ctx.state_manager.transition(
        task.task_id, TaskState.SUCCESS, result="All tests pass"
    )

    # Verify final state
    final = ctx.state_manager.get_task(task.task_id)
    assert final.state == TaskState.SUCCESS
    assert final.result == "All tests pass"
    assert final.is_terminal

    # Verify events were published (create + 3 transitions = 4)
    assert len(events_received) == 4
    assert events_received[0].to_state == "PENDING"
    assert events_received[1].to_state == "ROUTING"
    assert events_received[2].to_state == "WORKING"
    assert events_received[3].to_state == "SUCCESS"

    # Verify no active tasks remain
    assert ctx.state_manager.get_active_tasks() == []

    await ctx.shutdown()


@pytest.mark.asyncio
async def test_end_to_end_with_parent_child_tasks():
    """Integration: parent task with children demonstrates the full
    hierarchy and cascade cancellation.
    """
    ctx = create_app()
    await ctx.startup()

    parent = await ctx.state_manager.create_task("Build feature X")
    child1 = await ctx.state_manager.create_task(
        "Research design patterns", parent_id=parent.task_id
    )
    child2 = await ctx.state_manager.create_task(
        "Write implementation", parent_id=parent.task_id
    )

    # Move parent to ROUTING
    await ctx.state_manager.transition(parent.task_id, TaskState.ROUTING)

    # Cancel the parent — should cascade to children
    await ctx.state_manager.cancel_task_tree(parent.task_id)

    assert ctx.state_manager.get_task(parent.task_id).state == TaskState.CANCELLED
    assert ctx.state_manager.get_task(child1.task_id).state == TaskState.CANCELLED
    assert ctx.state_manager.get_task(child2.task_id).state == TaskState.CANCELLED

    await ctx.shutdown()


@pytest.mark.asyncio
async def test_event_bus_drains_on_shutdown():
    """Shutdown should wait for in-flight background tasks to complete."""
    ctx = create_app()
    await ctx.startup()

    completed = []

    async def slow_handler(event):
        await asyncio.sleep(0.05)
        completed.append(True)

    ctx.event_bus.subscribe("UserRequestEvent", slow_handler)

    # Fire-and-forget via emit
    await ctx.event_bus.emit(UserRequestEvent(source="test", text="hello"))
    # Shutdown should wait for the slow handler
    await ctx.shutdown()

    assert completed == [True]
    assert ctx.event_bus.state == BusState.STOPPED
