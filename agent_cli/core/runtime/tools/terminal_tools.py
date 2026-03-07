"""Agent-facing tools for managing persistent terminals."""

from __future__ import annotations

from typing import Any, Literal, Type

from pydantic import BaseModel, Field, field_validator

from agent_cli.core.runtime.services.terminal_manager import TerminalManager
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory


class SpawnTerminalArgs(BaseModel):
    """Arguments for the ``spawn_terminal`` tool."""

    command: str = Field(
        description="The shell command to run as a persistent terminal.",
    )

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        command = value.strip()
        if not command:
            raise ValueError("command must not be empty.")
        return command


class ReadTerminalArgs(BaseModel):
    """Arguments for the ``read_terminal`` tool."""

    terminal_id: str = Field(description="ID of the terminal to read from.")
    last_n: int | None = Field(
        default=None,
        description=(
            "Optional cap on returned lines. When consume=true, older unread lines "
            "are still marked as read."
        ),
    )
    consume: bool = Field(
        default=True,
        description=(
            "When true, return only unread lines since the previous read and advance "
            "the read cursor. Set false to inspect the current retained buffer "
            "without advancing."
        ),
    )

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        terminal_id = value.strip()
        if not terminal_id:
            raise ValueError("terminal_id must not be empty.")
        return terminal_id

    @field_validator("last_n")
    @classmethod
    def _validate_last_n(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("last_n must be non-negative.")
        return value


class SendTerminalInputArgs(BaseModel):
    """Arguments for the ``send_terminal_input`` tool."""

    terminal_id: str = Field(description="ID of the terminal to send input to.")
    text: str = Field(
        description=(
            "Text to write to the terminal's stdin (include \\n if needed). "
            "Use read_terminal after a brief pause to inspect the response."
        )
    )

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        terminal_id = value.strip()
        if not terminal_id:
            raise ValueError("terminal_id must not be empty.")
        return terminal_id


class WaitForTerminalArgs(BaseModel):
    """Arguments for the ``wait_for_terminal`` tool."""

    terminal_id: str = Field(description="ID of the terminal to wait on.")
    pattern: str = Field(
        description=(
            "Text or regex pattern to wait for in new terminal output. Prefer a "
            "stable readiness signal such as 'Done' or 'listening on'."
        )
    )
    timeout: float | None = Field(
        default=None,
        description=(
            "Optional timeout in seconds. Omit to use the configured default; values "
            "are clamped to the configured maximum."
        ),
    )
    mode: Literal["literal", "regex"] = Field(
        default="literal",
        description=(
            "Pattern matching mode. Use literal by default; regex is available for "
            "cases where simple substring matching is not sufficient."
        ),
    )

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        terminal_id = value.strip()
        if not terminal_id:
            raise ValueError("terminal_id must not be empty.")
        return terminal_id

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str) -> str:
        pattern = value.strip()
        if not pattern:
            raise ValueError("pattern must not be empty.")
        return pattern

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("timeout must be positive.")
        return value


class KillTerminalArgs(BaseModel):
    """Arguments for the ``kill_terminal`` tool."""

    terminal_id: str = Field(description="ID of the terminal to kill.")

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        terminal_id = value.strip()
        if not terminal_id:
            raise ValueError("terminal_id must not be empty.")
        return terminal_id


class ListTerminalsArgs(BaseModel):
    """Arguments for the ``list_terminals`` tool."""


class SpawnTerminalTool(BaseTool):
    """Spawn a persistent terminal process."""

    name = "spawn_terminal"
    description = (
        "Start a persistent shell command that keeps running after the tool call "
        "returns. Use this for dev servers, watchers, and long-running tasks."
    )
    is_safe = False
    parallel_safe = False
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return SpawnTerminalArgs

    async def execute(self, **kwargs: Any) -> str:
        command = str(kwargs.get("command", ""))
        terminal_id = await self._terminal_manager.spawn(command)
        return f"Spawned terminal {terminal_id}: {command}"


class ReadTerminalTool(BaseTool):
    """Read buffered output from a persistent terminal."""

    name = "read_terminal"
    description = (
        "Read buffered output from a persistent terminal. By default this returns "
        "only new lines since the previous read; set consume=false to inspect the "
        "current retained buffer without advancing."
    )
    is_safe = True
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return ReadTerminalArgs

    async def execute(self, **kwargs: Any) -> str:
        terminal_id = str(kwargs.get("terminal_id", ""))
        last_n = kwargs.get("last_n")
        consume = bool(kwargs.get("consume", True))
        output = self._terminal_manager.read(
            terminal_id,
            last_n=last_n,
            consume=consume,
        )
        if output:
            return output
        return "[No new output yet]" if consume else "[No output yet]"


class SendTerminalInputTool(BaseTool):
    """Send stdin input to a persistent terminal."""

    name = "send_terminal_input"
    description = "Send stdin input to a persistent terminal."
    is_safe = False
    parallel_safe = False
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return SendTerminalInputArgs

    async def execute(self, **kwargs: Any) -> str:
        terminal_id = str(kwargs.get("terminal_id", ""))
        text = str(kwargs.get("text", ""))
        await self._terminal_manager.send_input(terminal_id, text)
        return f"Sent input to terminal {terminal_id}."


class WaitForTerminalTool(BaseTool):
    """Wait for new terminal output to match a pattern."""

    name = "wait_for_terminal"
    description = (
        "Wait for new output from a persistent terminal to match a pattern. "
        "This is the preferred way to synchronize on server readiness without "
        "repeated polling."
    )
    is_safe = True
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return WaitForTerminalArgs

    async def execute(self, **kwargs: Any) -> str:
        terminal_id = str(kwargs.get("terminal_id", ""))
        pattern = str(kwargs.get("pattern", ""))
        timeout = kwargs.get("timeout")
        mode = str(kwargs.get("mode", "literal"))
        result = await self._terminal_manager.wait_for_output(
            terminal_id,
            pattern,
            timeout=timeout,
            mode=mode,
        )
        if result.matched and result.line is not None:
            return result.line
        if result.terminal_exited:
            return (
                f"Terminal {terminal_id} exited before pattern matched "
                f"(exit code {result.exit_code})."
            )
        return (
            f"Timed out after {result.effective_timeout:.1f}s waiting for {mode} "
            f"pattern in terminal {terminal_id}."
        )


class KillTerminalTool(BaseTool):
    """Terminate a persistent terminal."""

    name = "kill_terminal"
    description = "Terminate a persistent terminal and return its exit code."
    is_safe = False
    parallel_safe = False
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return KillTerminalArgs

    async def execute(self, **kwargs: Any) -> str:
        terminal_id = str(kwargs.get("terminal_id", ""))
        exit_code = await self._terminal_manager.kill(terminal_id)
        return f"Killed terminal {terminal_id} (exit code {exit_code})."


class ListTerminalsTool(BaseTool):
    """List all known persistent terminals."""

    name = "list_terminals"
    description = "List all persistent terminals with status and exit code."
    is_safe = True
    category = ToolCategory.TERMINAL

    def __init__(self, terminal_manager: TerminalManager) -> None:
        self._terminal_manager = terminal_manager

    @property
    def args_schema(self) -> Type[BaseModel]:
        return ListTerminalsArgs

    async def execute(self, **kwargs: Any) -> str:
        terminals = self._terminal_manager.list_terminals()
        if not terminals:
            return "No terminals."

        lines: list[str] = []
        for item in terminals:
            terminal_id = str(item.get("terminal_id", ""))
            command = str(item.get("command", ""))
            exited = bool(item.get("exited", False))
            exit_code = item.get("exit_code")
            status = "exited" if exited else "running"
            exit_suffix = f", exit_code={exit_code}" if exited else ""
            lines.append(
                f"{terminal_id}: {status}{exit_suffix}, command={command}"
            )
        return "\n".join(lines)


__all__ = [
    "KillTerminalArgs",
    "KillTerminalTool",
    "ListTerminalsArgs",
    "ListTerminalsTool",
    "ReadTerminalArgs",
    "ReadTerminalTool",
    "SendTerminalInputArgs",
    "SendTerminalInputTool",
    "SpawnTerminalArgs",
    "SpawnTerminalTool",
    "WaitForTerminalArgs",
    "WaitForTerminalTool",
]
