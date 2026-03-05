"""Tests for `/sessions` UI command and session auto-save lifecycle hooks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.ux.commands.base import CommandContext
from agent_cli.core.ux.commands.parser import CommandParser
from agent_cli.core.infra.registry.bootstrap import AppContext, _build_command_registry
from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import SettingsChangedEvent, TaskResultEvent
from agent_cli.core.runtime.orchestrator.state_manager import TaskStateManager
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.session.file_store import FileSessionManager


class _DummyToolRegistry:
    def get_all_names(self):
        return []


class _DummySessionOverlay:
    def __init__(self) -> None:
        self.opened = False

    def show_overlay(self) -> None:
        self.opened = True


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
        data_registry=DataRegistry(),
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
    registry = _build_command_registry()
    return CommandParser(registry=registry, context=ctx)


@pytest.mark.asyncio
async def test_autosave_on_task_result_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    session_dir = tmp_path / "sessions"
    settings = AgentSettings(default_model="gpt-4o-mini", session_auto_save=True)
    app_context = _build_app_context(session_dir=session_dir, settings=settings)
    monkeypatch.setattr(
        DataRegistry,
        "get_session_defaults",
        lambda self: {"auto_save_interval_seconds": 999.0},
    )
    await app_context.startup()

    active = app_context.session_manager.create_session()
    app_context.session_manager.save(active)
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

    active = app_context.session_manager.create_session()
    app_context.session_manager.save(active)
    active.total_cost = 4.56
    active_id = active.session_id

    await app_context.shutdown()

    reloader = FileSessionManager(session_dir=session_dir, default_model="gpt-4o-mini")
    restored = reloader.load(active_id)
    assert restored.total_cost == pytest.approx(4.56)


@pytest.mark.asyncio
async def test_periodic_autosave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    session_dir = tmp_path / "sessions"
    settings = AgentSettings(default_model="gpt-4o-mini", session_auto_save=True)
    app_context = _build_app_context(session_dir=session_dir, settings=settings)
    monkeypatch.setattr(
        DataRegistry,
        "get_session_defaults",
        lambda self: {"auto_save_interval_seconds": 0.1},
    )
    await app_context.startup()

    active = app_context.session_manager.create_session()
    app_context.session_manager.save(active)
    active.total_cost = 7.89
    active_id = active.session_id

    await asyncio.sleep(0.25)

    reloader = FileSessionManager(session_dir=session_dir, default_model="gpt-4o-mini")
    restored = reloader.load(active_id)
    assert restored.total_cost == pytest.approx(7.89)

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_sessions_command_opens_overlay(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    overlay = _DummySessionOverlay()
    dummy_app = SimpleNamespace(session_overlay=overlay)

    ctx = CommandContext(
        settings=app_context.settings,
        event_bus=app_context.event_bus,
        state_manager=app_context.state_manager,
        memory_manager=app_context.memory_manager,
        app=dummy_app,
        app_context=SimpleNamespace(
            session_manager=app_context.session_manager,
            providers=SimpleNamespace(),
            orchestrator=None,
        ),
    )
    parser = _build_parser(ctx)

    result = await parser.execute("/sessions")
    assert result.success is True
    assert result.message == ""
    assert overlay.opened is True

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_session_command_is_removed(tmp_path: Path):
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
            providers=SimpleNamespace(),
            orchestrator=None,
        ),
    )
    parser = _build_parser(ctx)

    result = await parser.execute("/session list")
    assert result.success is False
    assert "Unknown command: /session" in result.message

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_generate_title_updates_active_session_name(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    active = app_context.session_manager.create_session()
    active.messages = [
        {"role": "user", "content": "Plan a release checklist for next sprint"},
        {"role": "assistant", "content": "I can draft milestones and owners."},
    ]
    app_context.session_manager.save(active)

    class _Provider:
        async def safe_generate(self, **kwargs):
            return SimpleNamespace(text_content="Sprint Release Checklist")

    updates: list[SettingsChangedEvent] = []

    async def _on_settings(event):
        if isinstance(event, SettingsChangedEvent):
            updates.append(event)

    app_context.event_bus.subscribe("SettingsChangedEvent", _on_settings)

    ctx = CommandContext(
        settings=app_context.settings,
        event_bus=app_context.event_bus,
        state_manager=app_context.state_manager,
        memory_manager=app_context.memory_manager,
        app_context=SimpleNamespace(
            session_manager=app_context.session_manager,
            providers=SimpleNamespace(),
            orchestrator=SimpleNamespace(
                active_agent=SimpleNamespace(provider=_Provider())
            ),
        ),
    )
    parser = _build_parser(ctx)

    result = await parser.execute("/generate_title")
    assert result.success is True
    assert "Sprint Release Checklist" in result.message

    restored = app_context.session_manager.get_active()
    assert restored is not None
    assert restored.name == "Sprint Release Checklist"
    assert any(e.setting_name == "session_title" for e in updates)

    await app_context.shutdown()


@pytest.mark.asyncio
async def test_generate_title_falls_back_when_model_output_empty(tmp_path: Path):
    session_dir = tmp_path / "sessions"
    app_context = _build_app_context(session_dir=session_dir)
    await app_context.startup()

    active = app_context.session_manager.create_session()
    active.messages = [{"role": "user", "content": "Hello"}]
    app_context.session_manager.save(active)

    class _Provider:
        async def safe_generate(self, **kwargs):
            return SimpleNamespace(text_content="")

    ctx = CommandContext(
        settings=app_context.settings,
        event_bus=app_context.event_bus,
        state_manager=app_context.state_manager,
        memory_manager=app_context.memory_manager,
        app_context=SimpleNamespace(
            session_manager=app_context.session_manager,
            providers=SimpleNamespace(),
            orchestrator=SimpleNamespace(
                active_agent=SimpleNamespace(provider=_Provider())
            ),
        ),
    )
    parser = _build_parser(ctx)

    result = await parser.execute("/generate_title")
    assert result.success is True
    assert "Untitled session" in result.message
    restored = app_context.session_manager.get_active()
    assert restored is not None
    assert restored.name == "Untitled session"

    await app_context.shutdown()
