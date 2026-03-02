"""Tests for sandbox workspace manager and /sandbox command integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_cli.commands.base import CommandContext, CommandRegistry
from agent_cli.commands.parser import CommandParser
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.state.state_manager import TaskStateManager
from agent_cli.workspace.sandbox import SandboxWorkspaceManager
from agent_cli.workspace.strict import StrictWorkspaceManager


class _MockSettings:
    default_model = "gpt-4o-mini"
    max_iterations = 100
    auto_approve_tools = False
    show_agent_thinking = True
    log_level = "INFO"
    tool_output_max_chars = 5000


class _MockMemory:
    def reset_working(self):
        return None

    def count_tokens(self) -> int:
        return 0

    async def on_model_changed(self, model_name: str, **kwargs):
        return False


def _build_lazy_sandbox(tmp_path: Path) -> SandboxWorkspaceManager:
    strict = StrictWorkspaceManager(root_path=tmp_path)
    sandbox = SandboxWorkspaceManager(strict)
    sandbox._is_git_repo = lambda: False  # type: ignore[assignment]
    return sandbox


@pytest.mark.asyncio
async def test_sandbox_lazy_discard_restores_originals(tmp_path: Path):
    sandbox = _build_lazy_sandbox(tmp_path)

    original = tmp_path / "a.txt"
    original.write_text("before", encoding="utf-8")

    status = sandbox.enable()
    assert status.active is True
    assert status.mode == "lazy"

    sandbox.resolve_path("a.txt", writable=True).write_text("after", encoding="utf-8")
    sandbox.resolve_path("b.txt", writable=True).write_text("new", encoding="utf-8")

    changes = sandbox.list_changes()
    assert any(line == "M a.txt" for line in changes)
    assert any(line == "A b.txt" for line in changes)

    sandbox.disable("discard")
    assert original.read_text(encoding="utf-8") == "before"
    assert not (tmp_path / "b.txt").exists()


@pytest.mark.asyncio
async def test_sandbox_lazy_apply_keeps_changes(tmp_path: Path):
    sandbox = _build_lazy_sandbox(tmp_path)

    file_path = tmp_path / "keep.txt"
    file_path.write_text("old", encoding="utf-8")

    sandbox.enable()
    sandbox.resolve_path("keep.txt", writable=True).write_text("new", encoding="utf-8")
    sandbox.disable("apply")

    assert file_path.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_sandbox_command_flow(tmp_path: Path):
    sandbox = _build_lazy_sandbox(tmp_path)

    import agent_cli.commands.handlers.sandbox  # noqa: F401
    from agent_cli.commands.base import _DEFAULT_REGISTRY

    registry = CommandRegistry()
    registry.absorb(_DEFAULT_REGISTRY)

    event_bus = AsyncEventBus()
    ctx = CommandContext(
        settings=_MockSettings(),  # type: ignore[arg-type]
        event_bus=event_bus,  # type: ignore[arg-type]
        state_manager=TaskStateManager(event_bus),  # type: ignore[arg-type]
        memory_manager=_MockMemory(),  # type: ignore[arg-type]
        app_context=SimpleNamespace(workspace_manager=sandbox),
    )
    parser = CommandParser(registry=registry, context=ctx)

    on_result = await parser.execute("/sandbox on")
    assert on_result.success is True
    assert "LAZY" in on_result.message

    sandbox.resolve_path("x.txt", writable=True).write_text("x", encoding="utf-8")
    ls_result = await parser.execute("/sandbox ls")
    assert ls_result.success is True
    assert "A x.txt" in ls_result.message

    off_result = await parser.execute("/sandbox off discard")
    assert off_result.success is True
    assert "discard" in off_result.message.lower()
