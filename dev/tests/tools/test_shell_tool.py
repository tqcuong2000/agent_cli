import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.tools import shell_tool
from agent_cli.core.runtime.tools.shell_tool import (
    RunCommandTool,
    compile_safe_command_patterns,
    is_safe_command,
)
from agent_cli.core.runtime.tools.workspace import WorkspaceContext


@pytest.fixture
def workspace(tmp_path: Path):
    return WorkspaceContext(root_path=tmp_path)


def test_run_command_parallel_safe_flag(workspace):
    tool = RunCommandTool(workspace, data_registry=DataRegistry())
    assert tool.parallel_safe is False


def test_is_safe_command():
    patterns = compile_safe_command_patterns(DataRegistry())
    assert is_safe_command("ls -la", patterns)
    assert is_safe_command("cat src/main.py", patterns)
    assert is_safe_command("echo hello", patterns)
    assert is_safe_command("pwd", patterns)
    assert is_safe_command("git status", patterns)
    assert is_safe_command("pytest tests/", patterns)
    assert is_safe_command("python -c 'print()'", patterns)

    assert not is_safe_command("rm -rf /", patterns)
    assert not is_safe_command("python script.py", patterns)
    assert not is_safe_command("docker build .", patterns)
    assert not is_safe_command("git push", patterns)


@pytest.mark.asyncio
async def test_run_command_tool_success(workspace, tmp_path):
    tool = RunCommandTool(workspace, data_registry=DataRegistry())

    # Simple echo
    res = await tool.execute("echo hello", timeout=5)
    assert "[Exit Code: 0]" in res
    assert "hello" in res


@pytest.mark.asyncio
async def test_run_command_tool_stderr(workspace, tmp_path):
    tool = RunCommandTool(workspace, data_registry=DataRegistry())

    # Simple error
    res = await tool.execute('python -c "1/0"', timeout=5)
    assert "[Exit Code:" in res
    assert "ZeroDivisionError" in res
    assert "[stderr]" in res


@pytest.mark.asyncio
async def test_run_command_tool_timeout(workspace, tmp_path):
    tool = RunCommandTool(workspace, data_registry=DataRegistry())

    # Use a sleep command that ignores the small timeout
    with pytest.raises(ToolExecutionError, match="Command timed out after 1s"):
        await tool.execute('python -c "import time; time.sleep(5)"', timeout=1)


@pytest.mark.asyncio
async def test_run_command_uses_devnull_and_strips_terminal_control_sequences(
    workspace, monkeypatch
):
    tool = RunCommandTool(workspace, data_registry=DataRegistry())
    captured: dict[str, object] = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            # Includes xterm mouse report + ANSI color + OSC title + control chars.
            stdout = b"prefix \x1b[<35;63;19M color=\x1b[31mred\x1b[0m \x00\x01 done\n"
            stderr = b"\x1b]0;title\x07err\x1b[2K\n"
            return stdout, stderr

    async def _fake_create_subprocess_shell(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(
        shell_tool.asyncio,
        "create_subprocess_shell",
        _fake_create_subprocess_shell,
    )

    result = await tool.execute("echo hello", timeout=5)

    assert captured.get("stdin") == asyncio.subprocess.DEVNULL
    assert "\x1b" not in result
    assert "prefix" in result
    assert "color=red" in result
    assert "done" in result
    assert "err" in result
