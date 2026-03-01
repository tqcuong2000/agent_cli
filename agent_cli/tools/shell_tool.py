"""
Shell Tool — execute shell commands with safety checks.

The ``RunCommandTool`` executes short-lived blocking commands with a
timeout.  Dangerous commands require user approval; safe commands
(``ls``, ``cat``, ``echo``, etc.) are auto-approved via dynamic regex.

For long-running processes (servers, watchers), use the terminal tools
(``spawn_terminal``, etc.) instead — those are a Phase 5 concern.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Type

from pydantic import BaseModel, Field

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.tools.workspace import WorkspaceContext

# ══════════════════════════════════════════════════════════════════════
# Safe Command Patterns
# ══════════════════════════════════════════════════════════════════════

# Commands matching any of these patterns are considered safe and
# skip the user-approval gate.
_SAFE_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        r"^(ls|dir|cat|type|echo|pwd|cd|head|tail|wc|grep|find|which|whoami|date|env)\b",
        r"^python\s+-c\s+['\"]print\b",
        r"^(git\s+(status|log|diff|branch|show))\b",
        r"^(pip|uv)\s+(list|show|freeze)\b",
        r"^pytest\b",
        r"^(node|python|ruby|go)\s+--version\b",
    ]
]


def is_safe_command(command: str) -> bool:
    """Check if a command matches any known safe pattern.

    Returns ``True`` if the command is safe (no approval needed).
    """
    stripped = command.strip()
    return any(pattern.match(stripped) for pattern in _SAFE_COMMAND_PATTERNS)


# ══════════════════════════════════════════════════════════════════════
# RunCommand Tool
# ══════════════════════════════════════════════════════════════════════


class RunCommandArgs(BaseModel):
    """Arguments for the ``run_command`` tool."""

    command: str = Field(description="The shell command to execute.")
    timeout: int = Field(
        default=30,
        description="Timeout in seconds (max 120).",
    )


class RunCommandTool(BaseTool):
    """Execute a blocking shell command and return stdout/stderr.

    For short-lived commands only (max 120s timeout).  For long-running
    processes, use ``spawn_terminal`` instead.

    Safety:
        By default ``is_safe = False``, meaning the ``ToolExecutor``
        requests user approval.  However, the executor checks
        ``is_safe_command()`` to auto-approve harmless commands like
        ``ls``, ``cat``, ``echo``, etc.
    """

    name = "run_command"
    description = (
        "Execute a shell command and return its stdout/stderr. "
        "For short-lived commands only (max 120s timeout). "
        "For long-running processes, use spawn_terminal instead."
    )
    is_safe = False  # Requires approval (dynamic regex may override)
    category = ToolCategory.EXECUTION

    def __init__(self, workspace: WorkspaceContext) -> None:
        self.workspace = workspace

    @property
    def args_schema(self) -> Type[BaseModel]:
        return RunCommandArgs

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 30)
        timeout = min(max(int(timeout), 1), 120)  # Clamp to [1, 120]

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace.root_path),
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ToolExecutionError(
                f"Command timed out after {timeout}s: {command[:100]}",
                tool_name=self.name,
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        output_parts: list[str] = [f"[Exit Code: {exit_code}]"]
        if stdout_text.strip():
            output_parts.append(stdout_text)
        if stderr_text.strip():
            output_parts.append(f"[stderr]\n{stderr_text}")

        return "\n".join(output_parts)
