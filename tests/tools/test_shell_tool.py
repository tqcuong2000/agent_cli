import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.tools.shell_tool import RunCommandTool, is_safe_command
from agent_cli.tools.workspace import WorkspaceContext


@pytest.fixture
def workspace(tmp_path: Path):
    return WorkspaceContext(root_path=tmp_path)


def test_is_safe_command():
    assert is_safe_command("ls -la")
    assert is_safe_command("cat src/main.py")
    assert is_safe_command("echo hello")
    assert is_safe_command("pwd")
    assert is_safe_command("git status")
    assert is_safe_command("pytest tests/")
    assert is_safe_command("python -c 'print()'")

    assert not is_safe_command("rm -rf /")
    assert not is_safe_command("python script.py")
    assert not is_safe_command("docker build .")
    assert not is_safe_command("git push")


@pytest.mark.asyncio
async def test_run_command_tool_success(workspace, tmp_path):
    tool = RunCommandTool(workspace)

    # Simple echo
    res = await tool.execute("echo hello", timeout=5)
    assert "[Exit Code: 0]" in res
    assert "hello" in res


@pytest.mark.asyncio
async def test_run_command_tool_stderr(workspace, tmp_path):
    tool = RunCommandTool(workspace)

    # Simple error
    res = await tool.execute("python -c \"1/0\"", timeout=5)
    assert "[Exit Code:" in res
    assert "ZeroDivisionError" in res
    assert "[stderr]" in res


@pytest.mark.asyncio
async def test_run_command_tool_timeout(workspace, tmp_path):
    tool = RunCommandTool(workspace)

    # Use a sleep command that ignores the small timeout
    with pytest.raises(ToolExecutionError, match="Command timed out after 1s"):
        await tool.execute("python -c \"import time; time.sleep(5)\"", timeout=1)
