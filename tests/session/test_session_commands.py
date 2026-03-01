"""Tests for `/session` commands and auto-save lifecycle hooks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.commands.base import CommandContext, CommandRegistry
from agent_cli.commands.parser import CommandParser
from agent_cli.core.bootstrap import AppContext
from agent_cli.core.config import AgentSettings
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import TaskResultEvent
from agent_cli.core.state.state_manager import TaskStateManager
from agent_cli.memory.token_counter import HeuristicTokenCounter
from agent_cli.session.file_store import FileSessionManager


class _DummyToolRegistry:
    def get_all_names(self):
        return []


def _build_app_context(
    *,
    session_dir: Path,
    settings: AgentSettings | None = None,
) -> AppContext:
    cfg = settings or AgentSettings(default_model="gpt-4o-mini")
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus=event_bus)
    session_manager = FileSessionManager(
        session_dir=session_dir,
        default_model=cfg.default_model,
    )
    memory_manager = WorkingMemoryManager()

    return AppContext(
        settings=cfg,
        event_bus=event_bus,
        state_manager=state_manager,
        providers=SimpleNamespace(),
        tool_registry=_DummyToolRegistry(),  # type: ignore[arg-type]
        tool_executor=SimpleNamespace(),  # type: ignore[arg-type]
        schema_validator=SimpleNamespace(),  # type: ignore[arg-type]
        memory_manager=memory_manager,
        prompt_builder=SimpleNamespace(),  # type: ignore[arg-type]
        orchestrator=None,
        session_manager=session_manager,
        command_registry=None,
        command_parser=None,
        interaction_handler=None,
        file_tracker=None,
    )


def _build_parser(ctx: CommandContext) -> CommandParser:
    import agent_cli.commands.handlers.session  # noqa: F401
    from agent_cli.commands.base import _DEFAULT_REGISTRY

    registry = CommandRegistry()
    registry.absorb(_DEFAULT_REGISTRY)
    return CommandParser(registry=registry, context=ctx)


@pytest.mark.asyncio
async def test_session_commands_save_list_info_restore(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    ctx = CommandContext(
        settings=app_context.settings,
        event_bus=app_context.event_bus,
        state_manager=app_context.state_manager,
        memory_manager=app_context.memory_manager,
        app_context=SimpleNamespace(
            session_manager=app_context.session_manager,
            providers=SimpleNamespace(
                get_token_counter=lambda _model: HeuristicTokenCounter()
            ),
            orchestrator=None,
        ),
    )
    parser = _build_parser(ctx)

    save_result = await parser.execute("/session save alpha")
    assert save_result.success is True
    active = app_context.session_manager.get_active()
    assert active is not None
    first_session_id = active.session_id

    active.messages.append({"role": "user", "content": "remember this"})
    app_context.session_manager.save(active)

    new_result = await parser.execute("/session new beta")
    assert new_result.success is True
    second_session = app_context.session_manager.get_active()
    assert second_session is not None
    assert second_session.session_id != first_session_id

    list_result = await parser.execute("/session list")
    assert list_result.success is True
    assert first_session_id in list_result.message
    assert second_session.session_id in list_result.message

    restore_result = await parser.execute(f"/session restore {first_session_id[:8]}")
    assert restore_result.success is True
    working_context = app_context.memory_manager.get_working_context()
    assert any(msg.get("content") == "remember this" for msg in working_context)

    info_result = await parser.execute("/session info")
    assert info_result.success is True
    assert "messages: 1" in info_result.message

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_session_delete_active_creates_replacement(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    ctx = CommandContext(
        settings=app_context.settings,
        event_bus=app_context.event_bus,
        state_manager=app_context.state_manager,
        memory_manager=app_context.memory_manager,
        app_context=SimpleNamespace(
            session_manager=app_context.session_manager,
            providers=SimpleNamespace(
                get_token_counter=lambda _model: HeuristicTokenCounter()
            ),
            orchestrator=None,
        ),
    )
    parser = _build_parser(ctx)

    active_before = app_context.session_manager.get_active()
    assert active_before is not None
    old_id = active_before.session_id

    delete_result = await parser.execute(f"/session delete {old_id}")
    assert delete_result.success is True

    active_after = app_context.session_manager.get_active()
    assert active_after is not None
    assert active_after.session_id != old_id

    assert app_context.session_manager.delete(old_id) is False
    await app_context.shutdown()


@pytest.mark.asyncio
async def test_autosave_on_task_result_event(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    settings = AgentSettings(
        default_model="gpt-4o-mini",
        session_auto_save=True,
        session_auto_save_interval_seconds=999.0,
    )
    app_context = _build_app_context(session_dir=session_dir, settings=settings)
    await app_context.startup()

    active = app_context.session_manager.get_active()
    assert active is not None
    active.total_cost = 1.23

    await app_context.event_bus.publish(
        TaskResultEvent(source="test", task_id="task-1", result="ok", is_success=True)
    )

    reloader = FileSessionManager(session_dir=session_dir, default_model="gpt-4o-mini")
    restored = reloader.load(active.session_id)
    assert restored.total_cost == pytest.approx(1.23)

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_autosave_on_shutdown(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    active = app_context.session_manager.get_active()
    assert active is not None
    active.total_cost = 4.56
    active_id = active.session_id

    await app_context.shutdown()

    reloader = FileSessionManager(session_dir=session_dir, default_model="gpt-4o-mini")
    restored = reloader.load(active_id)
    assert restored.total_cost == pytest.approx(4.56)


@pytest.mark.asyncio
async def test_periodic_autosave(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    settings = AgentSettings(
        default_model="gpt-4o-mini",
        session_auto_save=True,
        session_auto_save_interval_seconds=0.1,
    )
    app_context = _build_app_context(session_dir=session_dir, settings=settings)
    await app_context.startup()

    active = app_context.session_manager.get_active()
    assert active is not None
    active.total_cost = 7.89
    active_id = active.session_id

    await asyncio.sleep(0.25)

    reloader = FileSessionManager(session_dir=session_dir, default_model="gpt-4o-mini")
    restored = reloader.load(active_id)
    assert restored.total_cost == pytest.approx(7.89)

    await app_context.shutdown()
