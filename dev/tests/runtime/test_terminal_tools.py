from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agent_cli.core.runtime.services import TerminalWaitResult
from agent_cli.core.runtime.tools.base import ToolCategory
from agent_cli.core.runtime.tools.terminal_tools import (
    KillTerminalTool,
    ListTerminalsTool,
    ReadTerminalTool,
    SendTerminalInputTool,
    SpawnTerminalTool,
    WaitForTerminalTool,
)


def _mock_manager() -> MagicMock:
    manager = MagicMock()
    manager.spawn = AsyncMock(return_value="term_1234")
    manager.read = MagicMock(return_value="line one\nline two")
    manager.send_input = AsyncMock(return_value=None)
    manager.wait_for_output = AsyncMock(
        return_value=TerminalWaitResult(
            matched=True,
            line="server ready",
            effective_timeout=30.0,
        )
    )
    manager.kill = AsyncMock(return_value=143)
    manager.list_terminals = MagicMock(
        return_value=[
            {
                "terminal_id": "term_1234",
                "command": "npm run dev",
                "exited": False,
                "exit_code": None,
            }
        ]
    )
    return manager


def test_terminal_tool_metadata() -> None:
    manager = _mock_manager()
    tools = [
        SpawnTerminalTool(manager),
        ReadTerminalTool(manager),
        SendTerminalInputTool(manager),
        WaitForTerminalTool(manager),
        KillTerminalTool(manager),
        ListTerminalsTool(manager),
    ]

    assert [tool.name for tool in tools] == [
        "spawn_terminal",
        "read_terminal",
        "send_terminal_input",
        "wait_for_terminal",
        "kill_terminal",
        "list_terminals",
    ]
    assert all(tool.category == ToolCategory.TERMINAL for tool in tools)
    assert SpawnTerminalTool(manager).is_safe is False
    assert ReadTerminalTool(manager).is_safe is True
    assert SendTerminalInputTool(manager).is_safe is False
    assert WaitForTerminalTool(manager).is_safe is True
    assert KillTerminalTool(manager).is_safe is False
    assert ListTerminalsTool(manager).is_safe is True


def test_terminal_tool_args_validation() -> None:
    manager = _mock_manager()

    with pytest.raises(ValidationError):
        SpawnTerminalTool(manager).validate_args(command="   ")
    with pytest.raises(ValidationError):
        ReadTerminalTool(manager).validate_args(terminal_id="", last_n=1)
    with pytest.raises(ValidationError):
        ReadTerminalTool(manager).validate_args(terminal_id="term_1", last_n=-1)
    with pytest.raises(ValidationError):
        SendTerminalInputTool(manager).validate_args(terminal_id=" ", text="y\n")
    with pytest.raises(ValidationError):
        WaitForTerminalTool(manager).validate_args(terminal_id=" ", pattern="ready")
    with pytest.raises(ValidationError):
        WaitForTerminalTool(manager).validate_args(terminal_id="term_1", pattern=" ")
    with pytest.raises(ValidationError):
        WaitForTerminalTool(manager).validate_args(
            terminal_id="term_1",
            pattern="ready",
            timeout=0,
        )
    with pytest.raises(ValidationError):
        KillTerminalTool(manager).validate_args(terminal_id=" ")

    assert (
        SpawnTerminalTool(manager).validate_args(command="npm run dev").command
        == "npm run dev"
    )
    assert (
        ReadTerminalTool(manager).validate_args(terminal_id="term_1", last_n=5).last_n
        == 5
    )
    assert (
        ReadTerminalTool(manager)
        .validate_args(terminal_id="term_1", consume=False)
        .consume
        is False
    )
    assert (
        WaitForTerminalTool(manager)
        .validate_args(terminal_id="term_1", pattern="ready", mode="regex")
        .mode
        == "regex"
    )
    assert ListTerminalsTool(manager).validate_args().model_dump() == {}


@pytest.mark.asyncio
async def test_spawn_terminal_tool_delegates_to_manager() -> None:
    manager = _mock_manager()
    tool = SpawnTerminalTool(manager)

    result = await tool.execute(command="npm run dev")

    manager.spawn.assert_awaited_once_with("npm run dev")
    assert "term_1234" in result
    assert "npm run dev" in result


@pytest.mark.asyncio
async def test_read_terminal_tool_delegates_to_manager() -> None:
    manager = _mock_manager()
    tool = ReadTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234", last_n=2, consume=False)

    manager.read.assert_called_once_with("term_1234", last_n=2, consume=False)
    assert result == "line one\nline two"


@pytest.mark.asyncio
async def test_read_terminal_tool_returns_placeholder_when_empty() -> None:
    manager = _mock_manager()
    manager.read.return_value = ""
    tool = ReadTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234")

    assert result == "[No new output yet]"


@pytest.mark.asyncio
async def test_read_terminal_tool_snapshot_placeholder_when_empty() -> None:
    manager = _mock_manager()
    manager.read.return_value = ""
    tool = ReadTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234", consume=False)

    assert result == "[No output yet]"


@pytest.mark.asyncio
async def test_send_terminal_input_tool_delegates_to_manager() -> None:
    manager = _mock_manager()
    tool = SendTerminalInputTool(manager)

    result = await tool.execute(terminal_id="term_1234", text="y\n")

    manager.send_input.assert_awaited_once_with("term_1234", "y\n")
    assert result == "Sent input to terminal term_1234."


@pytest.mark.asyncio
async def test_wait_for_terminal_tool_delegates_to_manager() -> None:
    manager = _mock_manager()
    tool = WaitForTerminalTool(manager)

    result = await tool.execute(
        terminal_id="term_1234",
        pattern="ready",
        timeout=15,
        mode="regex",
    )

    manager.wait_for_output.assert_awaited_once_with(
        "term_1234",
        "ready",
        timeout=15,
        mode="regex",
    )
    assert result == "server ready"


@pytest.mark.asyncio
async def test_wait_for_terminal_tool_formats_timeout() -> None:
    manager = _mock_manager()
    manager.wait_for_output = AsyncMock(
        return_value=TerminalWaitResult(
            matched=False,
            timed_out=True,
            effective_timeout=12.0,
        )
    )
    tool = WaitForTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234", pattern="ready")

    assert result == "Timed out after 12.0s waiting for literal pattern in terminal term_1234."


@pytest.mark.asyncio
async def test_wait_for_terminal_tool_formats_exit_before_match() -> None:
    manager = _mock_manager()
    manager.wait_for_output = AsyncMock(
        return_value=TerminalWaitResult(
            matched=False,
            terminal_exited=True,
            exit_code=1,
            effective_timeout=30.0,
        )
    )
    tool = WaitForTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234", pattern="ready")

    assert result == "Terminal term_1234 exited before pattern matched (exit code 1)."


@pytest.mark.asyncio
async def test_kill_terminal_tool_delegates_to_manager() -> None:
    manager = _mock_manager()
    tool = KillTerminalTool(manager)

    result = await tool.execute(terminal_id="term_1234")

    manager.kill.assert_awaited_once_with("term_1234")
    assert result == "Killed terminal term_1234 (exit code 143)."


@pytest.mark.asyncio
async def test_list_terminals_tool_formats_manager_output() -> None:
    manager = _mock_manager()
    tool = ListTerminalsTool(manager)

    result = await tool.execute()

    manager.list_terminals.assert_called_once_with()
    assert "term_1234" in result
    assert "running" in result
    assert "npm run dev" in result


@pytest.mark.asyncio
async def test_list_terminals_tool_handles_empty_state() -> None:
    manager = _mock_manager()
    manager.list_terminals.return_value = []
    tool = ListTerminalsTool(manager)

    result = await tool.execute()

    assert result == "No terminals."
