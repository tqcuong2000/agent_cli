"""Tests for the Command System (Phase 4.2).

Covers:
- @command decorator registration
- CommandParser.is_command()
- CommandParser.execute() — success, failure, unknown
- get_suggestions() prefix matching
- /clear resets memory manager
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from agent_cli.agent.registry import AgentRegistry
from agent_cli.agent.session_registry import SessionAgentRegistry
from agent_cli.commands.base import (
    CommandContext,
    CommandDef,
    CommandRegistry,
    CommandResult,
    command,
)
from agent_cli.commands.parser import CommandParser

# ── Mock dependencies ────────────────────────────────────────────────


class _MockSettings:
    """Minimal settings stand-in."""

    def __init__(self) -> None:
        self.default_model = "gpt-4o"
        self.default_agent = "default"
        self.max_iterations = 100
        self.agents = {}
        self.auto_approve_tools = False
        self.show_agent_thinking = True
        self.log_level = "INFO"
        self.log_max_file_size_mb = 50
        self.tool_output_max_chars = 5000


class _MockMemoryManager:
    """Records calls for assertion."""

    def __init__(self) -> None:
        self.message_count = 5
        self._reset_called = False

    def reset_working(self) -> None:
        self._reset_called = True
        self.message_count = 0

    def count_tokens(self) -> int:
        return 42

    async def on_model_changed(self, model_name: str, **kwargs: Any) -> bool:
        return False


class _MockEventBus:
    """Stub event bus that ignores everything."""

    def __init__(self) -> None:
        self.published_events: List[Any] = []

    async def publish(self, *a: Any, **kw: Any) -> None:
        if a:
            self.published_events.append(a[0])

    def subscribe(self, *a: Any, **kw: Any) -> str:
        return "sub-1"


class _MockStateManager:
    pass


class _SimpleAgent:
    def __init__(self, name: str) -> None:
        self.name = name


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CommandRegistry:
    """Build a registry with the core handlers pre-loaded."""
    # Importing triggers @command decorators
    import agent_cli.commands.handlers.agent  # noqa: F401
    import agent_cli.commands.handlers.core  # noqa: F401
    import agent_cli.commands.handlers.sandbox  # noqa: F401
    import agent_cli.commands.handlers.session  # noqa: F401
    from agent_cli.commands.base import _DEFAULT_REGISTRY

    reg = CommandRegistry()
    reg.absorb(_DEFAULT_REGISTRY)
    return reg


@pytest.fixture
def ctx() -> CommandContext:
    """Lightweight CommandContext with mocks."""
    return CommandContext(
        settings=_MockSettings(),  # type: ignore[arg-type]
        event_bus=_MockEventBus(),  # type: ignore[arg-type]
        state_manager=_MockStateManager(),  # type: ignore[arg-type]
        memory_manager=_MockMemoryManager(),  # type: ignore[arg-type]
        app=None,
    )


@pytest.fixture
def parser(registry: CommandRegistry, ctx: CommandContext) -> CommandParser:
    return CommandParser(registry=registry, context=ctx)


# ── Tests ────────────────────────────────────────────────────────────


def test_command_decorator_registers_into_registry(registry: CommandRegistry):
    """@command decorator populates CommandRegistry."""
    names = [c.name for c in registry.all()]
    assert "help" in names
    assert "clear" in names
    assert "exit" in names
    assert "agent" in names
    assert "model" in names
    assert "debug" in names
    assert "config" in names
    assert "cost" in names
    assert "context" in names
    assert "sandbox" in names
    assert "sessions" in names


def test_parser_is_command_true_false():
    """is_command detects '/' prefix correctly."""
    assert CommandParser.is_command("/help") is True
    assert CommandParser.is_command("  /model gpt-4o") is True
    assert CommandParser.is_command("hello") is False
    assert CommandParser.is_command("") is False
    assert CommandParser.is_command("Fix the bug") is False


@pytest.mark.asyncio
async def test_parser_execute_help_returns_success(parser: CommandParser):
    """/help returns a successful result listing commands."""
    result = await parser.execute("/help")

    assert result.success is True
    assert "/help" in result.message
    assert "/model" in result.message


@pytest.mark.asyncio
async def test_parser_execute_unknown_returns_failure(parser: CommandParser):
    """/unknown returns a failure result."""
    result = await parser.execute("/nonexistent_command")

    assert result.success is False
    assert "Unknown command" in result.message


def test_get_suggestions_prefix_match(parser: CommandParser):
    """get_suggestions('mo') returns /model."""
    suggestions = parser.get_suggestions("mo")
    names = [s.name for s in suggestions]

    assert "model" in names


def test_get_suggestions_agent_prefix(parser: CommandParser):
    suggestions = parser.get_suggestions("ag")
    names = [s.name for s in suggestions]
    assert "agent" in names


@pytest.mark.asyncio
async def test_clear_resets_memory_manager(parser: CommandParser, ctx: CommandContext):
    """/clear calls memory_manager.reset_working()."""
    result = await parser.execute("/clear")

    assert result.success is True
    assert ctx.memory_manager._reset_called is True
    assert "cleared" in result.message.lower()


@pytest.mark.asyncio
async def test_model_command_updates_model_and_emits_settings_event(
    parser: CommandParser, ctx: CommandContext
):
    class _MockProviders:
        def get_token_counter(self, model_name: str) -> object:
            return object()

        def get_token_budget(self, model_name: str, **kwargs: Any) -> object:
            return object()

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=None,
        providers=_MockProviders(),
        data_registry=SimpleNamespace(
            get_context_budget=lambda: {"compaction_threshold": 0.80}
        ),
    )

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert ctx.settings.default_model == "gpt-4o-mini"
    assert any(
        getattr(event, "setting_name", "") == "default_model"
        for event in ctx.event_bus.published_events
    )


@pytest.mark.asyncio
async def test_debug_command_updates_log_level(
    parser: CommandParser, ctx: CommandContext
):
    result = await parser.execute("/debug on")
    assert result.success is True
    assert ctx.settings.log_level == "DEBUG"


@pytest.mark.asyncio
async def test_model_command_updates_active_agent_and_persists_override(
    parser: CommandParser, ctx: CommandContext
):
    class _MockProvider:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

    class _MockProviders:
        def get_provider(self, model_name: str) -> _MockProvider:
            return _MockProvider(model_name)

        def get_token_counter(self, model_name: str) -> object:
            return object()

        def get_token_budget(self, model_name: str, **kwargs: Any) -> object:
            return object()

    @dataclass
    class _AgentConfig:
        model: str = "gpt-4o"

    @dataclass
    class _MockAgent:
        name: str = "coder"
        config: _AgentConfig = field(default_factory=_AgentConfig)
        memory: Any = field(default_factory=_MockMemoryManager)
        provider: Any = field(default_factory=lambda: _MockProvider("gpt-4o"))

    active_agent = _MockAgent()

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=SimpleNamespace(active_agent=active_agent),
        providers=_MockProviders(),
        data_registry=SimpleNamespace(
            get_context_budget=lambda: {"compaction_threshold": 0.80}
        ),
    )

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert active_agent.config.model == "gpt-4o-mini"
    assert active_agent.provider.model_name == "gpt-4o-mini"
    assert ctx.settings.agents["coder"]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_sandbox_command_reports_missing_manager(parser: CommandParser):
    result = await parser.execute("/sandbox on")
    assert result.success is False
    assert "not configured" in result.message.lower()


@pytest.mark.asyncio
async def test_agent_command_list_add_and_default(
    parser: CommandParser, ctx: CommandContext
):
    global_registry = AgentRegistry()
    default_agent = _SimpleAgent("default")
    coder_agent = _SimpleAgent("coder")
    global_registry.register(default_agent)  # type: ignore[arg-type]
    global_registry.register(coder_agent)  # type: ignore[arg-type]

    session_registry = SessionAgentRegistry()
    session_registry.add(default_agent, activate=True)  # type: ignore[arg-type]

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=SimpleNamespace(
            session_agents=session_registry,
            agent_registry=global_registry,
        )
    )

    listed = await parser.execute("/agent")
    assert listed.success is True
    assert "default [ACTIVE]" in listed.message

    added = await parser.execute("/agent add coder")
    assert added.success is True
    assert "Added agent 'coder'" in added.message

    listed2 = await parser.execute("/agent")
    assert "coder [IDLE]" in listed2.message

    set_default = await parser.execute("/agent default coder")
    assert set_default.success is True
    assert ctx.settings.default_agent == "coder"


@pytest.mark.asyncio
async def test_agent_command_cannot_disable_active(
    parser: CommandParser, ctx: CommandContext
):
    global_registry = AgentRegistry()
    default_agent = _SimpleAgent("default")
    global_registry.register(default_agent)  # type: ignore[arg-type]

    session_registry = SessionAgentRegistry()
    session_registry.add(default_agent, activate=True)  # type: ignore[arg-type]

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=SimpleNamespace(
            session_agents=session_registry,
            agent_registry=global_registry,
        )
    )

    result = await parser.execute("/agent disable default")
    assert result.success is False
    assert "Cannot disable the active agent" in result.message
