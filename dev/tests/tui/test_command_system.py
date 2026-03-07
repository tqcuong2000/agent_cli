"""Tests for the Command System (Phase 4.2).

Covers:
- explicit command registry wiring
- CommandParser.is_command()
- CommandParser.execute() — success, failure, unknown
- get_suggestions() prefix matching
- /clear resets memory manager
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from agent_cli.core.runtime.agents.registry import AgentRegistry
from agent_cli.core.runtime.agents.session_registry import SessionAgentRegistry
from agent_cli.core.ux.commands.base import (
    CommandContext,
    CommandDef,
    CommandRegistry,
    CommandResult,
)
from agent_cli.core.ux.commands.parser import CommandParser
from agent_cli.core.ux.commands.handlers.core import cycle_effort
from agent_cli.core.infra.registry.bootstrap import _build_command_registry
from agent_cli.core.infra.registry.registry import DataRegistry

# ── Mock dependencies ────────────────────────────────────────────────


class _MockSettings:
    """Minimal settings stand-in."""

    def __init__(self) -> None:
        self.default_model = "gpt-4o"
        self.default_effort = "auto"
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

    async def handle_task(self, task: Any) -> str:
        return str(task)


async def _noop_handler(args: List[str], ctx: CommandContext) -> CommandResult:
    return CommandResult(success=True, message="ok")


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CommandRegistry:
    """Build a registry with explicit bootstrap wiring."""
    return _build_command_registry()


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
    ctx.app_context = SimpleNamespace(command_registry=registry)  # type: ignore[assignment]
    return CommandParser(registry=registry, context=ctx)


# ── Tests ────────────────────────────────────────────────────────────


def test_command_decorator_registers_into_registry(registry: CommandRegistry):
    """Built-in command registry contains expected core commands."""
    names = [c.name for c in registry.all()]
    assert "help" in names
    assert "clear" in names
    assert "exit" in names
    assert "agent" in names
    assert "model" in names
    assert "debug" in names
    assert "config" in names
    assert "connect" in names
    assert "cost" in names
    assert "context" in names
    assert "sandbox" in names
    assert "sessions" in names


def test_command_registry_duplicate_guard() -> None:
    reg = CommandRegistry()
    reg.register(
        CommandDef(
            name="help",
            description="v1",
            usage="/help",
            handler=_noop_handler,
        )
    )
    with pytest.raises(ValueError, match="already registered"):
        reg.register(
            CommandDef(
                name="help",
                description="v2",
                usage="/help",
                handler=_noop_handler,
            )
        )


def test_command_registry_override_replaces_definition() -> None:
    reg = CommandRegistry()
    reg.register(
        CommandDef(
            name="help",
            description="v1",
            usage="/help",
            handler=_noop_handler,
        )
    )
    reg.register(
        CommandDef(
            name="help",
            description="v2",
            usage="/help",
            handler=_noop_handler,
        ),
        override=True,
    )
    resolved = reg.get("help")
    assert resolved is not None
    assert resolved.description == "v2"


def test_command_registry_rejects_missing_handler() -> None:
    reg = CommandRegistry()
    with pytest.raises(ValueError, match="callable handler"):
        reg.register(
            CommandDef(
                name="help",
                description="missing handler",
                usage="/help",
                handler=None,  # type: ignore[arg-type]
            )
        )


def test_command_registry_supports_len_and_contains() -> None:
    reg = CommandRegistry()
    reg.register(
        CommandDef(
            name="help",
            description="v1",
            usage="/help",
            handler=_noop_handler,
        )
    )

    assert len(reg) == 1
    assert "help" in reg
    assert "HELP" in reg
    assert "model" not in reg


def test_command_registry_freeze_blocks_register() -> None:
    reg = CommandRegistry()
    reg.register(
        CommandDef(
            name="help",
            description="v1",
            usage="/help",
            handler=_noop_handler,
        )
    )
    reg.freeze()
    assert reg.is_frozen is True
    with pytest.raises(RuntimeError, match="frozen"):
        reg.register(
            CommandDef(
                name="model",
                description="switch model",
                usage="/model",
                handler=_noop_handler,
            )
        )


def test_command_registry_freeze_rejects_empty_registry() -> None:
    reg = CommandRegistry()
    with pytest.raises(RuntimeError, match="at least one command"):
        reg.freeze()


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


@pytest.mark.asyncio
async def test_parser_execute_effort_returns_unknown(parser: CommandParser):
    result = await parser.execute("/effort high")
    assert result.success is False
    assert "Unknown command: /effort" in result.message


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
    ctx.settings.default_effort = "high"

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert ctx.settings.default_model == "gpt-4o-mini"
    assert ctx.settings.default_effort == "auto"
    assert any(
        getattr(event, "setting_name", "") == "default_model"
        for event in ctx.event_bus.published_events
    )
    assert any(
        getattr(event, "setting_name", "") == "effort"
        and getattr(event, "new_value", "") == "auto"
        for event in ctx.event_bus.published_events
    )


@pytest.mark.asyncio
async def test_model_command_updates_tui_status_model(parser: CommandParser, ctx: CommandContext):
    class _MockStatus:
        def __init__(self) -> None:
            self.model = ""
            self.agent = ""
            self.effort = ""

        def update_model(self, value: str) -> None:
            self.model = value

        def update_active_agent(self, value: str) -> None:
            self.agent = value

        def update_effort(self, value: str) -> None:
            self.effort = value

    class _MockBadge:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value: str) -> None:
            self.value = value

    class _MockApp:
        def __init__(self) -> None:
            self.status = _MockStatus()
            self.badge = _MockBadge()

        def query_one(self, cls: Any) -> Any:
            if cls.__name__ == "StatusContainer":
                return self.status
            if cls.__name__ == "AgentBadgeComponent":
                return self.badge
            raise LookupError(cls)

    class _MockProviders:
        def get_token_counter(self, model_name: str) -> object:
            return object()

        def get_token_budget(self, model_name: str, **kwargs: Any) -> object:
            return object()

    mock_app = _MockApp()
    ctx.app = mock_app  # type: ignore[assignment]
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=None,
        providers=_MockProviders(),
        data_registry=SimpleNamespace(
            get_context_budget=lambda: {"compaction_threshold": 0.80}
        ),
    )

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert mock_app.status.model == "gpt-4o-mini"
    assert mock_app.status.effort == "auto"


@pytest.mark.asyncio
async def test_connect_without_args_opens_provider_overlay(
    parser: CommandParser, ctx: CommandContext
):
    class _Overlay:
        def __init__(self) -> None:
            self.opened = False

        def show_overlay(self) -> None:
            self.opened = True

    overlay = _Overlay()
    ctx.app = SimpleNamespace(provider_overlay=overlay)  # type: ignore[assignment]

    result = await parser.execute("/connect")

    assert result.success is True
    assert result.message == ""
    assert overlay.opened is True


@pytest.mark.asyncio
async def test_connect_direct_sets_key_via_key_manager(
    parser: CommandParser, ctx: CommandContext, registry: CommandRegistry
):
    class _KeyManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def set_key(self, provider_name: str, env_var: str, value: str) -> bool:
            self.calls.append((provider_name, env_var, value))
            return True

    key_manager = _KeyManager()
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        command_registry=registry,
        key_manager=key_manager,
        data_registry=SimpleNamespace(
            get_provider_specs=lambda: {
                "google": SimpleNamespace(
                    require_verification=True,
                    api_key_env="GOOGLE_API_KEY",
                )
            }
        ),
    )

    result = await parser.execute("/connect google sk-test-123")

    assert result.success is True
    assert result.message == "API key set for google."
    assert key_manager.calls == [("google", "GOOGLE_API_KEY", "sk-test-123")]


@pytest.mark.asyncio
async def test_connect_direct_rejects_unknown_provider(
    parser: CommandParser, ctx: CommandContext, registry: CommandRegistry
):
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        command_registry=registry,
        key_manager=SimpleNamespace(set_key=lambda *args, **kwargs: True),
        data_registry=SimpleNamespace(get_provider_specs=lambda: {}),
    )

    result = await parser.execute("/connect mystery sk-test")

    assert result.success is False
    assert result.message == "Unknown provider: mystery"


@pytest.mark.asyncio
async def test_connect_direct_rejects_provider_without_key_requirement(
    parser: CommandParser, ctx: CommandContext, registry: CommandRegistry
):
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        command_registry=registry,
        key_manager=SimpleNamespace(set_key=lambda *args, **kwargs: True),
        data_registry=SimpleNamespace(
            get_provider_specs=lambda: {
                "ollama": SimpleNamespace(
                    require_verification=False,
                    api_key_env=None,
                )
            }
        ),
    )

    result = await parser.execute("/connect ollama sk-test")

    assert result.success is False
    assert result.message == "ollama does not require an API key."






@pytest.mark.asyncio
async def test_debug_command_updates_log_level(
    parser: CommandParser, ctx: CommandContext
):
    result = await parser.execute("/debug on")
    assert result.success is True
    assert ctx.settings.log_level == "DEBUG"


@pytest.mark.asyncio
async def test_cycle_effort_uses_active_model_supported_levels(ctx: CommandContext):
    class _Provider:
        provider_name = "openai"
        model_name = "gpt-4o-mini"
        base_url = ""

    class _DataRegistry:
        def get_capability_snapshot(self, **kwargs: Any) -> Any:
            _ = kwargs
            return SimpleNamespace(
                declared=SimpleNamespace(
                    effort=SimpleNamespace(supported=True, levels=["auto", "low", "high"])
                ),
                effective={
                    "effort": SimpleNamespace(status="supported", reason="declared_supported")
                },
            )

    ctx.settings.default_effort = "auto"
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        data_registry=_DataRegistry(),
        orchestrator=SimpleNamespace(
            active_agent=SimpleNamespace(provider=_Provider())
        ),
        session_manager=None,
    )

    result = await cycle_effort(ctx)
    assert result.success is True
    assert ctx.settings.default_effort == "low"
    assert any(
        getattr(event, "setting_name", "") == "effort"
        and getattr(event, "new_value", "") == "low"
        for event in ctx.event_bus.published_events
    )


@pytest.mark.asyncio
async def test_cost_command_uses_injected_observability(
    parser: CommandParser, ctx: CommandContext, registry: CommandRegistry
):
    class _Metrics:
        @staticmethod
        def to_summary() -> dict[str, Any]:
            return {
                "cost_usd": 0.012345,
                "llm_calls": 3,
                "tokens": {"input": 11, "output": 22, "total": 33},
            }

    class _Observability:
        metrics = _Metrics()

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        command_registry=registry,
        observability=_Observability(),
    )

    result = await parser.execute("/cost")
    assert result.success is True
    assert "Total cost: $0.012345" in result.message
    assert "LLM calls: 3" in result.message
    assert "Total tokens: 33" in result.message


@pytest.mark.asyncio
async def test_debug_command_uses_injected_observability(
    parser: CommandParser, ctx: CommandContext, registry: CommandRegistry
):
    class _Observability:
        def __init__(self) -> None:
            self.levels: list[str] = []

        def set_level(self, level: str) -> None:
            self.levels.append(level)

    obs = _Observability()
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        command_registry=registry,
        observability=obs,
    )

    result = await parser.execute("/debug on")
    assert result.success is True
    assert ctx.settings.log_level == "DEBUG"
    assert obs.levels == ["DEBUG"]

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
        plain_text: bool = False

    @dataclass
    class _MockAgent:
        name: str = "coder"
        config: _AgentConfig = field(default_factory=_AgentConfig)
        memory: Any = field(default_factory=_MockMemoryManager)
        provider: Any = field(default_factory=lambda: _MockProvider("gpt-4o"))

    active_agent = _MockAgent()
    probe_calls: list[str] = []

    class _Probe:
        def probe_provider(self, provider: Any, *, trigger: str) -> None:
            probe_calls.append(trigger)

    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=SimpleNamespace(active_agent=active_agent),
        providers=_MockProviders(),
        capability_probe=_Probe(),
        data_registry=SimpleNamespace(
            get_context_budget=lambda: {"compaction_threshold": 0.80},
            resolve_model_spec=lambda model_name: SimpleNamespace(
                plain_text=(model_name == "gpt-4o-mini")
            ),
        ),
    )

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert active_agent.config.model == "gpt-4o-mini"
    assert active_agent.config.plain_text is True
    assert active_agent.provider.model_name == "gpt-4o-mini"
    assert ctx.settings.agents["coder"]["model"] == "gpt-4o-mini"
    assert probe_calls == ["model_switch"]


@pytest.mark.asyncio
async def test_model_command_probe_failure_does_not_fail_switch(
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
        plain_text: bool = False

    @dataclass
    class _MockAgent:
        name: str = "coder"
        config: _AgentConfig = field(default_factory=_AgentConfig)
        memory: Any = field(default_factory=_MockMemoryManager)
        provider: Any = field(default_factory=lambda: _MockProvider("gpt-4o"))

    class _Probe:
        def probe_provider(self, provider: Any, *, trigger: str) -> None:
            raise RuntimeError("probe failed")

    active_agent = _MockAgent()
    ctx.app_context = SimpleNamespace(  # type: ignore[assignment]
        orchestrator=SimpleNamespace(active_agent=active_agent),
        providers=_MockProviders(),
        capability_probe=_Probe(),
        data_registry=SimpleNamespace(
            get_context_budget=lambda: {"compaction_threshold": 0.80},
            resolve_model_spec=lambda _model_name: None,
        ),
    )

    result = await parser.execute("/model gpt-4o-mini")

    assert result.success is True
    assert active_agent.config.model == "gpt-4o-mini"
    assert active_agent.config.plain_text is False


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
