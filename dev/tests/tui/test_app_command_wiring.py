from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_cli.core.infra.events.events import SettingsChangedEvent
from agent_cli.core.ux.commands.base import CommandResult
from agent_cli.core.ux.tui.app import AgentCLIApp
from agent_cli.core.ux.tui.views.header.title import TitleComponent


@pytest.fixture(autouse=True)
def _stable_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep app wiring tests deterministic regardless of local config."""
    monkeypatch.setenv("AGENT_DEFAULT_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "mock_key_for_testing")


def test_app_binds_command_parser_to_textual_app(tmp_path):
    app = AgentCLIApp(root_folder=str(tmp_path))
    parser = app.app_context.command_parser

    assert parser is not None
    assert parser.app is app
    assert app.app_context.interaction_handler is None


@pytest.mark.asyncio
async def test_app_mount_binds_interaction_handler_to_tool_executor(tmp_path):
    app = AgentCLIApp(root_folder=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.app_context.interaction_handler is not None
        assert (
            app.app_context.tool_executor._interaction_handler
            is app.app_context.interaction_handler
        )


@pytest.mark.asyncio
async def test_escape_action_interrupts_active_agent(tmp_path):
    app = AgentCLIApp(root_folder=str(tmp_path))
    calls = {"count": 0}
    notices: list[str] = []

    async def _interrupt() -> bool:
        calls["count"] += 1
        return True

    app.app_context.orchestrator = SimpleNamespace(interrupt_active_task=_interrupt)
    app.notify = lambda message, **kwargs: notices.append(str(message))  # type: ignore[method-assign]

    await app.action_interrupt_agent()

    assert calls["count"] == 1
    assert any("Stopping current task" in notice for notice in notices)


@pytest.mark.asyncio
async def test_session_title_event_updates_header_and_app_title(tmp_path):
    app = AgentCLIApp(root_folder=str(tmp_path))

    async with app.run_test() as pilot:
        await app.app_context.event_bus.publish(
            SettingsChangedEvent(
                source="test",
                setting_name="session_title",
                new_value="My Session",
            )
        )
        await pilot.pause()

        title_widget = app.query_one(TitleComponent)
        assert str(title_widget.content) == "My Session"
        assert app.title == "Engine CLI - My Session"


@pytest.mark.asyncio
async def test_ctrl_e_action_cycles_effort_without_notification(tmp_path, monkeypatch):
    app = AgentCLIApp(root_folder=str(tmp_path))
    notices: list[str] = []
    app.notify = lambda message, **kwargs: notices.append(str(message))  # type: ignore[method-assign]
    calls = {"count": 0}

    async def _fake_cycle(_ctx):
        calls["count"] += 1
        return CommandResult(success=True, message="Effort: high")

    monkeypatch.setattr(
        "agent_cli.core.ux.commands.handlers.core.cycle_effort",
        _fake_cycle,
    )

    await app.action_cycle_effort()
    assert calls["count"] == 1
    assert notices == []
