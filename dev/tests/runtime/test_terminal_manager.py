from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.events.event_bus import AbstractEventBus
from agent_cli.core.infra.events.events import (
    BaseEvent,
    TerminalExitedEvent,
    TerminalLogEvent,
    TerminalSpawnedEvent,
)
from agent_cli.core.runtime.services.terminal_manager import (
    TerminalManager,
    TerminalWaitResult,
)


class RecordingEventBus(AbstractEventBus):
    def __init__(self) -> None:
        self.published: list[BaseEvent] = []
        self.emitted: list[BaseEvent] = []

    async def publish(self, event: BaseEvent) -> None:
        self.published.append(event)

    async def emit(self, event: BaseEvent) -> None:
        self.emitted.append(event)

    def subscribe(self, event_type: str, callback, priority: int = 0) -> str:
        return "noop"

    def unsubscribe(self, subscription_id: str) -> None:
        return None

    async def drain(self) -> None:
        return None


async def _wait_for(
    predicate,
    *,
    timeout: float = 3.0,
    interval: float = 0.02,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition.")


@pytest.fixture
def event_bus() -> RecordingEventBus:
    return RecordingEventBus()


@pytest.fixture
def manager(tmp_path: Path, event_bus: RecordingEventBus) -> TerminalManager:
    return TerminalManager(
        event_bus,
        tmp_path,
        max_terminals=3,
        max_buffer_lines=20,
    )


def _python_command(script: str) -> str:
    escaped = script.replace('"', '\\"')
    return f'"{sys.executable}" -u -c "{escaped}"'


@pytest.mark.asyncio
async def test_spawn_terminal_returns_id_and_emits_spawn_event(
    manager: TerminalManager,
    event_bus: RecordingEventBus,
) -> None:
    terminal_id = await manager.spawn(
        _python_command("import time; print('hello', flush=True); time.sleep(0.5)")
    )

    assert terminal_id.startswith("term_")
    await _wait_for(
        lambda: any(
            isinstance(event, TerminalSpawnedEvent)
            and event.terminal_id == terminal_id
            for event in event_bus.published
        )
    )

    spawn_events = [
        event
        for event in event_bus.published
        if isinstance(event, TerminalSpawnedEvent)
    ]
    assert len(spawn_events) == 1
    assert spawn_events[0].terminal_id == terminal_id
    await manager.kill(terminal_id)


@pytest.mark.asyncio
async def test_read_terminal_returns_output(manager: TerminalManager) -> None:
    terminal_id = await manager.spawn(
        _python_command("import time; print('hello', flush=True); time.sleep(0.5)")
    )

    await _wait_for(lambda: "hello" in manager.read(terminal_id, consume=False))

    assert manager.read(terminal_id) == "hello"
    assert manager.read(terminal_id) == ""


@pytest.mark.asyncio
async def test_read_terminal_last_n(manager: TerminalManager) -> None:
    script = (
        "import sys, time; "
        "[print(f'line:{i}', flush=True) for i in range(5)]; "
        "time.sleep(0.5)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(lambda: "line:4" in manager.read(terminal_id, consume=False))

    assert manager.read(terminal_id, last_n=2) == "line:3\nline:4"
    assert manager.read(terminal_id) == ""


@pytest.mark.asyncio
async def test_read_terminal_consume_false_returns_snapshot_without_advancing(
    manager: TerminalManager,
) -> None:
    script = (
        "import sys, time; "
        "[print(f'line:{i}', flush=True) for i in range(3)]; "
        "time.sleep(0.5)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(lambda: "line:2" in manager.read(terminal_id, consume=False))

    assert manager.read(terminal_id, consume=False) == "line:0\nline:1\nline:2"
    assert manager.read(terminal_id, consume=False) == "line:0\nline:1\nline:2"
    assert manager.read(terminal_id) == "line:0\nline:1\nline:2"
    assert manager.read(terminal_id) == ""


@pytest.mark.asyncio
async def test_send_input_to_terminal(
    manager: TerminalManager,
    event_bus: RecordingEventBus,
) -> None:
    script = (
        "import sys; "
        "print('ready', flush=True); "
        "line = sys.stdin.readline().strip(); "
        "print('got:' + line, flush=True)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(lambda: "ready" in manager.read(terminal_id, consume=False))
    assert manager.read(terminal_id) == "ready"
    await manager.send_input(terminal_id, "ping\n")
    await _wait_for(lambda: "got:ping" in manager.read(terminal_id, consume=False))
    assert manager.read(terminal_id) == "got:ping"

    log_events = [
        event for event in event_bus.emitted if isinstance(event, TerminalLogEvent)
    ]
    assert any(event.content == "got:ping" for event in log_events)


@pytest.mark.asyncio
async def test_wait_for_output_matches_literal_without_consuming(
    manager: TerminalManager,
) -> None:
    script = (
        "import time; "
        "print('booting', flush=True); "
        "time.sleep(0.3); "
        "print('ready now', flush=True)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    result = await manager.wait_for_output(terminal_id, "ready", timeout=1.0)

    assert isinstance(result, TerminalWaitResult)
    assert result.matched is True
    assert result.line == "ready now"
    await _wait_for(lambda: "ready now" in manager.read(terminal_id, consume=False))
    assert manager.read(terminal_id) == "booting\nready now"


@pytest.mark.asyncio
async def test_wait_for_output_matches_regex(manager: TerminalManager) -> None:
    terminal_id = await manager.spawn(
        _python_command(
            "import time; print('port=25575', flush=True); time.sleep(0.5)"
        )
    )

    result = await manager.wait_for_output(
        terminal_id,
        r"port=\d+",
        timeout=1.0,
        mode="regex",
    )

    assert result.matched is True
    assert result.line == "port=25575"


@pytest.mark.asyncio
async def test_wait_for_output_timeout_preserves_unread_lines(
    manager: TerminalManager,
) -> None:
    script = (
        "import time; "
        "print('still booting', flush=True); "
        "time.sleep(0.5)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(
        lambda: "still booting" in manager.read(terminal_id, consume=False)
    )
    result = await manager.wait_for_output(terminal_id, "ready", timeout=0.1)

    assert result.matched is False
    assert result.timed_out is True
    assert manager.read(terminal_id) == "still booting"


@pytest.mark.asyncio
async def test_spawn_terminal_at_capacity_raises(
    tmp_path: Path,
    event_bus: RecordingEventBus,
) -> None:
    manager = TerminalManager(
        event_bus,
        tmp_path,
        max_terminals=1,
        max_buffer_lines=20,
    )

    terminal_id = await manager.spawn(
        _python_command("import time; print('running', flush=True); time.sleep(10)")
    )

    with pytest.raises(ToolExecutionError, match="Terminal limit reached"):
        await manager.spawn(_python_command("print('second', flush=True)"))

    await manager.kill(terminal_id)


@pytest.mark.asyncio
async def test_kill_terminal_returns_exit_code_and_emits_once(
    manager: TerminalManager,
    event_bus: RecordingEventBus,
) -> None:
    terminal_id = await manager.spawn(
        _python_command("import time; print('running', flush=True); time.sleep(10)")
    )

    await _wait_for(lambda: "running" in manager.read(terminal_id, consume=False))
    exit_code = await manager.kill(terminal_id)

    assert exit_code != 0
    exit_events = [
        event
        for event in event_bus.published
        if isinstance(event, TerminalExitedEvent)
        and event.terminal_id == terminal_id
    ]
    assert len(exit_events) == 1


@pytest.mark.asyncio
async def test_kill_terminal_uses_windows_process_tree_termination(
    manager: TerminalManager,
) -> None:
    terminal_id = await manager.spawn(
        _python_command("import time; print('running', flush=True); time.sleep(10)")
    )
    terminal = manager._get_terminal(terminal_id)
    called: dict[str, int] = {}

    async def _fake_taskkill(pid: int) -> None:
        called["pid"] = pid
        terminal.process.kill()

    manager._taskkill_process_tree = _fake_taskkill  # type: ignore[method-assign]

    await manager.kill(terminal_id)

    assert called["pid"] == terminal.process.pid


@pytest.mark.asyncio
async def test_buffer_size_limit(tmp_path: Path, event_bus: RecordingEventBus) -> None:
    manager = TerminalManager(
        event_bus,
        tmp_path,
        max_terminals=3,
        max_buffer_lines=2,
    )
    script = (
        "import sys, time; "
        "[print(f'line:{i}', flush=True) for i in range(5)]; "
        "time.sleep(0.5)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(lambda: "line:4" in manager.read(terminal_id, consume=False))

    assert manager.read(terminal_id, consume=False) == "line:3\nline:4"


@pytest.mark.asyncio
async def test_read_terminal_reports_dropped_unread_lines(
    tmp_path: Path,
    event_bus: RecordingEventBus,
) -> None:
    manager = TerminalManager(
        event_bus,
        tmp_path,
        max_terminals=3,
        max_buffer_lines=2,
    )
    script = (
        "import sys, time; "
        "[print(f'line:{i}', flush=True) for i in range(5)]; "
        "time.sleep(0.5)"
    )
    terminal_id = await manager.spawn(_python_command(script))

    await _wait_for(lambda: "line:4" in manager.read(terminal_id, consume=False))

    assert manager.read(terminal_id) == (
        "[3 earlier unread line(s) were dropped because the terminal buffer exceeded retention.]\n"
        "line:3\nline:4"
    )
    assert manager.read(terminal_id) == ""


@pytest.mark.asyncio
async def test_shutdown_kills_all(
    tmp_path: Path,
    event_bus: RecordingEventBus,
) -> None:
    manager = TerminalManager(
        event_bus,
        tmp_path,
        max_terminals=3,
        max_buffer_lines=20,
    )
    terminal_ids = [
        await manager.spawn(
            _python_command("import time; print('running', flush=True); time.sleep(10)")
        )
        for _ in range(2)
    ]

    await manager.shutdown()

    summaries = {item["terminal_id"]: item for item in manager.list_terminals()}
    for terminal_id in terminal_ids:
        assert summaries[terminal_id]["exited"] is True
        assert summaries[terminal_id]["exit_code"] is not None


@pytest.mark.asyncio
async def test_unknown_terminal_errors(manager: TerminalManager) -> None:
    with pytest.raises(ToolExecutionError, match="Unknown terminal"):
        manager.read("missing")

    with pytest.raises(ToolExecutionError, match="Unknown terminal"):
        await manager.kill("missing")


@pytest.mark.asyncio
async def test_spawn_empty_command_raises(manager: TerminalManager) -> None:
    with pytest.raises(ToolExecutionError, match="cannot be empty"):
        await manager.spawn("   ")


@pytest.mark.asyncio
async def test_spawn_terminal_immediate_exit_raises_with_output(
    manager: TerminalManager,
    event_bus: RecordingEventBus,
) -> None:
    with pytest.raises(ToolExecutionError, match="exited during startup") as exc_info:
        await manager.spawn(
            _python_command("import sys; print('boom', flush=True); sys.exit(2)")
        )

    assert "boom" in str(exc_info.value)
    assert "exit code 2" in str(exc_info.value)
    assert not any(
        isinstance(event, TerminalSpawnedEvent) for event in event_bus.published
    )
