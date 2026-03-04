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
from functools import lru_cache
from typing import Any, Type

from pydantic import BaseModel, Field

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.registry import DataRegistry
from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.workspace.base import BaseWorkspaceManager

# ══════════════════════════════════════════════════════════════════════
# Safe Command Patterns
# ══════════════════════════════════════════════════════════════════════

_SHELL_DEFAULTS = DataRegistry().get_tool_defaults().get("shell", {})
_DEFAULT_TIMEOUT = int(_SHELL_DEFAULTS.get("default_timeout", 30))
_MAX_TIMEOUT = int(_SHELL_DEFAULTS.get("max_timeout", 120))
_ANSI_CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1B\][^\x1B\x07]*(?:\x07|\x1B\\)")
_ANSI_SS3_RE = re.compile(r"\x1BO[@-~]")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0B-\x1A\x1C-\x1F\x7F]")


def is_safe_command(command: str) -> bool:
    """Check if a command matches any known safe pattern.

    Returns ``True`` if the command is safe (no approval needed).
    """
    stripped = command.strip()
    return any(pattern.match(stripped) for pattern in _compiled_safe_patterns())


# ══════════════════════════════════════════════════════════════════════
# RunCommand Tool
# ══════════════════════════════════════════════════════════════════════


class RunCommandArgs(BaseModel):
    """Arguments for the ``run_command`` tool."""

    command: str = Field(description="The shell command to execute.")
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
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

    def __init__(self, workspace: BaseWorkspaceManager) -> None:
        self.workspace = workspace

    @property
    def args_schema(self) -> Type[BaseModel]:
        return RunCommandArgs

    async def execute(
        self,
        command: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
        **kwargs: Any,
    ) -> str:
        timeout = min(max(int(timeout), 1), _MAX_TIMEOUT)  # Clamp to [1, max]

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(self.workspace.get_root()),
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

        stdout_text = _sanitize_terminal_output(
            stdout.decode("utf-8", errors="replace")
        )
        stderr_text = _sanitize_terminal_output(
            stderr.decode("utf-8", errors="replace")
        )
        exit_code = proc.returncode

        output_parts: list[str] = [f"[Exit Code: {exit_code}]"]
        if stdout_text.strip():
            output_parts.append(stdout_text)
        if stderr_text.strip():
            output_parts.append(f"[stderr]\n{stderr_text}")

        return "\n".join(output_parts)


@lru_cache(maxsize=1)
def _compiled_safe_patterns() -> tuple[re.Pattern[str], ...]:
    defaults = DataRegistry().get_safe_command_patterns()
    return tuple(re.compile(pattern) for pattern in defaults)


def _sanitize_terminal_output(text: str) -> str:
    """Strip terminal control sequences from command output.

    This prevents TUI mouse/keyboard escape streams and ANSI cursor control
    sequences from polluting the rendered transcript.
    """
    sanitized = _ANSI_OSC_RE.sub("", text)
    sanitized = _ANSI_CSI_RE.sub("", sanitized)
    sanitized = _ANSI_SS3_RE.sub("", sanitized)
    sanitized = _CTRL_CHARS_RE.sub("", sanitized)
    return sanitized
