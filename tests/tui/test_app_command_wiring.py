from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_cli.ux.tui.app import AgentCLIApp


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
