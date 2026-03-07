"""Persistent terminal lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.events.event_bus import AbstractEventBus
from agent_cli.core.infra.events.events import (
    TerminalExitedEvent,
    TerminalLogEvent,
    TerminalSpawnedEvent,
)
from agent_cli.core.runtime._subprocess import (
    ShellProfile,
    create_shell_subprocess,
    resolve_shell_profile,
)
from agent_cli.core.runtime.tools._sanitize import sanitize_terminal_output

logger = logging.getLogger(__name__)

_TERMINAL_TOOL_NAME = "terminal_manager"
_EXIT_WAIT_TIMEOUT_SECONDS = 5.0
_SPAWN_GRACE_PERIOD_SECONDS = 1.0
_SPAWN_OUTPUT_SETTLE_SECONDS = 0.2
_SPAWN_NO_OUTPUT_SUCCESS_SECONDS = 0.25
_SPAWN_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_WAIT_TIMEOUT_SECONDS = 30.0
_MAX_WAIT_TIMEOUT_SECONDS = 300.0


@dataclass(slots=True)
class ManagedTerminal:
    """In-memory state for one managed terminal process."""

    terminal_id: str
    command: str
    process: asyncio.subprocess.Process
    buffer: deque[str]
    created_at: float
    reader_task: asyncio.Task[None] | None = None
    exited: bool = False
    exit_code: int | None = None
    exit_event_emitted: bool = False
    buffer_start_index: int = 0
    next_line_index: int = 0
    next_unread_index: int = 0
    update_condition: asyncio.Condition = field(default_factory=asyncio.Condition)


@dataclass(slots=True)
class TerminalWaitResult:
    """Outcome of waiting for terminal output."""

    matched: bool
    line: str | None = None
    timed_out: bool = False
    terminal_exited: bool = False
    exit_code: int | None = None
    effective_timeout: float = 0.0


class TerminalManager:
    """App-scoped service for persistent subprocess terminals."""

    def __init__(
        self,
        event_bus: AbstractEventBus,
        workspace_root: Path,
        *,
        shell_profile: ShellProfile | None = None,
        max_terminals: int = 3,
        max_buffer_lines: int = 2000,
        default_wait_timeout: float = _DEFAULT_WAIT_TIMEOUT_SECONDS,
        max_wait_timeout: float = _MAX_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        self._event_bus = event_bus
        self._workspace_root = Path(workspace_root)
        self._shell_profile = shell_profile or resolve_shell_profile()
        self._max_terminals = max(int(max_terminals), 1)
        self._max_buffer_lines = max(int(max_buffer_lines), 1)
        self._default_wait_timeout = max(float(default_wait_timeout), 0.1)
        self._max_wait_timeout = max(
            float(max_wait_timeout),
            self._default_wait_timeout,
        )
        self._terminals: dict[str, ManagedTerminal] = {}

    async def spawn(self, command: str) -> str:
        """Start a persistent terminal and return its ID."""
        normalized_command = str(command).strip()
        if not normalized_command:
            raise ToolExecutionError(
                "Terminal command cannot be empty.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        if self._running_terminal_count() >= self._max_terminals:
            raise ToolExecutionError(
                f"Terminal limit reached ({self._max_terminals}).",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        process = await create_shell_subprocess(
            normalized_command,
            shell_profile=self._shell_profile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
            cwd=str(self._workspace_root),
        )

        terminal_id = f"term_{uuid.uuid4().hex[:8]}"
        terminal = ManagedTerminal(
            terminal_id=terminal_id,
            command=normalized_command,
            process=process,
            buffer=deque(maxlen=self._max_buffer_lines),
            created_at=time.time(),
        )
        terminal.reader_task = asyncio.create_task(
            self._reader_loop(terminal),
            name=f"terminal-reader:{terminal_id}",
        )
        self._terminals[terminal_id] = terminal

        if await self._exited_during_spawn_grace(terminal) and (
            terminal.exit_code not in (None, 0)
        ):
            await self._await_reader_shutdown(terminal)
            startup_output = self.read(terminal_id, consume=False)
            self._terminals.pop(terminal_id, None)
            message = (
                f"Terminal command exited during startup (exit code {terminal.exit_code})."
            )
            if startup_output.strip():
                message = f"{message}\n{startup_output}"
            raise ToolExecutionError(
                f"{message}\nUse run_command for short-lived commands.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        await self._event_bus.publish(
            TerminalSpawnedEvent(
                source="terminal_manager",
                terminal_id=terminal_id,
                command=normalized_command,
            )
        )
        return terminal_id

    def read(
        self,
        terminal_id: str,
        *,
        last_n: int | None = None,
        consume: bool = True,
    ) -> str:
        """Return buffered output for a terminal."""
        terminal = self._get_terminal(terminal_id)
        if last_n is not None:
            if int(last_n) < 0:
                raise ToolExecutionError(
                    "last_n must be non-negative.",
                    tool_name=_TERMINAL_TOOL_NAME,
                )
        start_index = terminal.next_unread_index if consume else terminal.buffer_start_index
        buffer_start_index = terminal.buffer_start_index
        buffer_end_index = terminal.next_line_index
        dropped_line_count = max(buffer_start_index - start_index, 0)
        effective_start_index = max(start_index, buffer_start_index)
        start_offset = max(effective_start_index - buffer_start_index, 0)
        lines = list(terminal.buffer)[start_offset:]

        if last_n is not None:
            limit = int(last_n)
            lines = [] if limit == 0 else lines[-limit:]

        if consume:
            terminal.next_unread_index = buffer_end_index

        if dropped_line_count > 0:
            notice = (
                f"[{dropped_line_count} earlier unread line(s) were dropped because "
                "the terminal buffer exceeded retention.]"
            )
            if lines:
                return "\n".join([notice, *lines])
            return notice

        return "\n".join(lines)

    async def send_input(self, terminal_id: str, text: str) -> None:
        """Write to a terminal's stdin."""
        terminal = self._get_terminal(terminal_id)
        if terminal.exited or terminal.process.returncode is not None:
            raise ToolExecutionError(
                f"Terminal '{terminal_id}' has already exited.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        stdin = terminal.process.stdin
        if stdin is None:
            raise ToolExecutionError(
                f"Terminal '{terminal_id}' does not accept stdin.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        stdin.write(str(text).encode("utf-8"))
        await stdin.drain()

    async def wait_for_output(
        self,
        terminal_id: str,
        pattern: str,
        *,
        timeout: float | None = None,
        mode: str = "literal",
    ) -> TerminalWaitResult:
        """Wait for terminal output matching a pattern from the unread cursor onward."""
        terminal = self._get_terminal(terminal_id)
        normalized_pattern = str(pattern)
        if not normalized_pattern:
            raise ToolExecutionError(
                "pattern must not be empty.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        normalized_mode = str(mode).strip().lower() or "literal"
        if normalized_mode not in {"literal", "regex"}:
            raise ToolExecutionError(
                "mode must be either 'literal' or 'regex'.",
                tool_name=_TERMINAL_TOOL_NAME,
            )

        matcher = self._build_matcher(normalized_pattern, normalized_mode)
        effective_timeout = self._resolve_wait_timeout(timeout)
        deadline = asyncio.get_running_loop().time() + effective_timeout
        start_index = max(terminal.next_unread_index, terminal.buffer_start_index)

        while True:
            matched_line = self._find_matching_line(
                terminal,
                matcher,
                start_index=start_index,
            )
            if matched_line is not None:
                return TerminalWaitResult(
                    matched=True,
                    line=matched_line,
                    effective_timeout=effective_timeout,
                )

            if terminal.exited:
                return TerminalWaitResult(
                    matched=False,
                    terminal_exited=True,
                    exit_code=terminal.exit_code,
                    effective_timeout=effective_timeout,
                )

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return TerminalWaitResult(
                    matched=False,
                    timed_out=True,
                    effective_timeout=effective_timeout,
                )

            observed_line_index = terminal.next_line_index
            observed_exited = terminal.exited
            changed = await self._wait_for_terminal_change(
                terminal,
                observed_line_index=observed_line_index,
                observed_exited=observed_exited,
                timeout=remaining,
            )
            if not changed:
                return TerminalWaitResult(
                    matched=False,
                    timed_out=True,
                    effective_timeout=effective_timeout,
                )

    async def kill(self, terminal_id: str) -> int:
        """Terminate a terminal process and return its exit code."""
        terminal = self._get_terminal(terminal_id)
        if terminal.exited:
            return int(terminal.exit_code or 0)

        if terminal.reader_task is not None:
            terminal.reader_task.cancel()

        if terminal.process.returncode is None:
            try:
                terminal.process.terminate()
            except ProcessLookupError:
                logger.debug("Terminal %s already stopped before terminate().", terminal_id)

        await self._await_reader_shutdown(terminal)
        return int(terminal.exit_code or 0)

    def list_terminals(self) -> list[dict[str, object]]:
        """Return a snapshot of all known terminals."""
        return [
            {
                "terminal_id": terminal.terminal_id,
                "command": terminal.command,
                "exited": terminal.exited,
                "exit_code": terminal.exit_code,
                "created_at": terminal.created_at,
            }
            for terminal in self._iter_terminals()
        ]

    async def shutdown(self) -> None:
        """Terminate all running terminals."""
        for terminal in list(self._iter_terminals()):
            if terminal.exited:
                continue
            try:
                await self.kill(terminal.terminal_id)
            except ToolExecutionError:
                logger.exception("Failed to shut down terminal %s", terminal.terminal_id)

    def _iter_terminals(self) -> Iterable[ManagedTerminal]:
        return sorted(
            self._terminals.values(),
            key=lambda terminal: terminal.created_at,
        )

    def _get_terminal(self, terminal_id: str) -> ManagedTerminal:
        normalized_id = str(terminal_id).strip()
        terminal = self._terminals.get(normalized_id)
        if terminal is None:
            raise ToolExecutionError(
                f"Unknown terminal: {normalized_id}",
                tool_name=_TERMINAL_TOOL_NAME,
            )
        return terminal

    def _running_terminal_count(self) -> int:
        return sum(1 for terminal in self._terminals.values() if not terminal.exited)

    async def _exited_during_spawn_grace(self, terminal: ManagedTerminal) -> bool:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + _SPAWN_GRACE_PERIOD_SECONDS
        first_output_seen_at: float | None = None

        while loop.time() < deadline:
            if terminal.process.returncode is not None:
                return True

            if terminal.next_line_index > 0:
                if first_output_seen_at is None:
                    first_output_seen_at = loop.time()
                elif (
                    loop.time() - first_output_seen_at
                    >= _SPAWN_OUTPUT_SETTLE_SECONDS
                ):
                    return False
            elif loop.time() - started_at >= _SPAWN_NO_OUTPUT_SUCCESS_SECONDS:
                return False

            await asyncio.sleep(_SPAWN_POLL_INTERVAL_SECONDS)

        return terminal.process.returncode is not None

    def _resolve_wait_timeout(self, timeout: float | None) -> float:
        effective_timeout = (
            self._default_wait_timeout if timeout is None else float(timeout)
        )
        return min(max(effective_timeout, 0.1), self._max_wait_timeout)

    def _build_matcher(
        self,
        pattern: str,
        mode: str,
    ) -> Callable[[str], bool]:
        if mode == "literal":
            return lambda line: pattern in line
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ToolExecutionError(
                f"Invalid regex pattern: {exc}",
                tool_name=_TERMINAL_TOOL_NAME,
            ) from exc
        return lambda line: compiled.search(line) is not None

    def _find_matching_line(
        self,
        terminal: ManagedTerminal,
        matcher: Callable[[str], bool],
        *,
        start_index: int,
    ) -> str | None:
        effective_start_index = max(start_index, terminal.buffer_start_index)
        start_offset = max(effective_start_index - terminal.buffer_start_index, 0)
        for line in list(terminal.buffer)[start_offset:]:
            if matcher(line):
                return line
        return None

    async def _wait_for_terminal_change(
        self,
        terminal: ManagedTerminal,
        *,
        observed_line_index: int,
        observed_exited: bool,
        timeout: float,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        async with terminal.update_condition:
            while (
                terminal.next_line_index == observed_line_index
                and terminal.exited == observed_exited
            ):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(
                        terminal.update_condition.wait(),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    return False
        return True

    async def _reader_loop(self, terminal: ManagedTerminal) -> None:
        stream = terminal.process.stdout
        was_cancelled = False
        try:
            if stream is None:
                return

            while True:
                raw_line = await stream.readline()
                if not raw_line:
                    break

                sanitized = sanitize_terminal_output(
                    raw_line.decode("utf-8", errors="replace")
                )
                line = sanitized.rstrip("\r\n")
                if terminal.buffer.maxlen is not None and len(terminal.buffer) == terminal.buffer.maxlen:
                    terminal.buffer_start_index += 1
                terminal.buffer.append(line)
                terminal.next_line_index += 1
                async with terminal.update_condition:
                    terminal.update_condition.notify_all()
                await self._event_bus.emit(
                    TerminalLogEvent(
                        source="terminal_manager",
                        terminal_id=terminal.terminal_id,
                        content=line,
                    )
                )
        except asyncio.CancelledError:
            was_cancelled = True
            current_task = asyncio.current_task()
            if current_task is not None:
                current_task.uncancel()
            logger.debug("Reader loop cancelled for terminal %s", terminal.terminal_id)
        finally:
            await self._mark_terminal_exited(terminal)
            if was_cancelled:
                logger.debug(
                    "Reader loop cleanup completed after cancellation for terminal %s",
                    terminal.terminal_id,
                )

    async def _await_reader_shutdown(self, terminal: ManagedTerminal) -> None:
        task = terminal.reader_task
        if task is None:
            await self._mark_terminal_exited(terminal)
            return

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_EXIT_WAIT_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            if terminal.process.returncode is None:
                await terminal.process.wait()
            await self._mark_terminal_exited(terminal)
        except asyncio.TimeoutError:
            logger.warning(
                "Terminal %s did not exit after %.1fs; forcing kill.",
                terminal.terminal_id,
                _EXIT_WAIT_TIMEOUT_SECONDS,
            )
            if terminal.process.returncode is None:
                terminal.process.kill()
            await terminal.process.wait()
            await task

    async def _mark_terminal_exited(self, terminal: ManagedTerminal) -> None:
        if terminal.exit_event_emitted:
            return

        if terminal.process.returncode is None:
            await terminal.process.wait()

        terminal.exited = True
        terminal.exit_code = int(terminal.process.returncode or 0)
        terminal.exit_event_emitted = True
        async with terminal.update_condition:
            terminal.update_condition.notify_all()
        await self._event_bus.publish(
            TerminalExitedEvent(
                source="terminal_manager",
                terminal_id=terminal.terminal_id,
                exit_code=terminal.exit_code,
            )
        )
