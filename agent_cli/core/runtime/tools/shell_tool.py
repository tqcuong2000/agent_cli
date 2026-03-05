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
from typing import Any, Iterable, Type

from pydantic import BaseModel, Field

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager

# ══════════════════════════════════════════════════════════════════════
# Safe Command Patterns
# ══════════════════════════════════════════════════════════════════════

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_ANSI_CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1B\][^\x1B\x07]*(?:\x07|\x1B\\)")
_ANSI_SS3_RE = re.compile(r"\x1BO[@-~]")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0B-\x1A\x1C-\x1F\x7F]")


def compile_safe_command_patterns(
    data_registry: DataRegistry,
) -> tuple[re.Pattern[str], ...]:
    defaults = data_registry.get_safe_command_patterns()
    return tuple(re.compile(pattern) for pattern in defaults)


def is_safe_command(
    command: str,
    safe_patterns: Iterable[re.Pattern[str]],
) -> bool:
    """Check if a command matches any known safe pattern.

    Returns ``True`` if the command is safe (no approval needed).
    """
    stripped = command.strip()
    return any(pattern.match(stripped) for pattern in safe_patterns)


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
    parallel_safe = False
    category = ToolCategory.EXECUTION

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self.workspace = workspace
        defaults = data_registry.get_tool_defaults().get("shell", {})
        self._default_timeout = int(defaults.get("default_timeout", _DEFAULT_TIMEOUT))
        self._max_timeout = int(defaults.get("max_timeout", _MAX_TIMEOUT))
        self._safe_patterns = compile_safe_command_patterns(data_registry)

    @property
    def args_schema(self) -> Type[BaseModel]:
        return RunCommandArgs

    async def execute(
        self,
        command: str = "",
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        effective_timeout = self._default_timeout if timeout is None else int(timeout)
        effective_timeout = min(max(effective_timeout, 1), self._max_timeout)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(self.workspace.get_root()),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ToolExecutionError(
                f"Command timed out after {effective_timeout}s: {command[:100]}",
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
