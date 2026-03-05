"""
Dependency Injection Bootstrap — wires all Phase 1 components together.

The ``create_app()`` factory is the **single entry point** that:

1. Loads configuration (``AgentSettings`` with TOML / env merge).
2. Creates the Event Bus.
3. Creates the State Manager (injected with the Event Bus).
4. Bundles everything into an ``AppContext`` — the component registry.
5. Provides lifecycle hooks: ``startup()`` and ``shutdown()``.

No component constructs its own dependencies — everything flows
through ``AppContext``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from agent_cli.core.runtime.agents.memory import BaseMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder

# Phase 3 imports
from agent_cli.core.runtime.agents.registry import AgentRegistry
from agent_cli.core.runtime.agents.schema import BaseSchemaValidator, SchemaValidator
from agent_cli.core.runtime.agents.session_registry import SessionAgentRegistry

# Phase 4.2 imports
from agent_cli.core.ux.commands.base import CommandContext, CommandDef, CommandRegistry
from agent_cli.core.ux.commands.parser import CommandParser
from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.events.event_bus import AbstractEventBus, AsyncEventBus
from agent_cli.core.infra.events.events import BaseEvent, TaskResultEvent
from agent_cli.core.runtime.orchestrator.file_tracker import FileChangeTracker
from agent_cli.core.infra.logging.logging import configure_observability
from agent_cli.core.runtime.orchestrator.orchestrator import Orchestrator
from agent_cli.core.runtime.orchestrator.state_manager import AbstractStateManager, TaskStateManager
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.cost.summarizer import SummarizingMemoryManager
from agent_cli.core.providers.base.capability_probe import CapabilityProbeService
from agent_cli.core.providers.manager import ProviderManager
from agent_cli.core.runtime.session.base import AbstractSessionManager
from agent_cli.core.runtime.session.file_store import FileSessionManager
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager
from agent_cli.core.ux.interaction.file_index import FileIndexer
from agent_cli.core.ux.interaction.sandbox import SandboxWorkspaceManager
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager

if TYPE_CHECKING:
    from agent_cli.core.ux.interaction.interaction import BaseInteractionHandler
    from agent_cli.core.infra.logging.logging import ObservabilityManager

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Component Registry (1.5.2)
# ══════════════════════════════════════════════════════════════════════


@dataclass
class AppContext:
    """Central component registry — the DI container for Agent CLI.

    Every component that needs a reference to another component
    receives it through this context object.  No global singletons.

    In later phases, additional fields will be added here
    (e.g. ``orchestrator``, ``memory_manager``, ``tool_registry``).
    """

    # ── Phase 1 Core ─────────────────────────────────────────────
    data_registry: DataRegistry
    settings: AgentSettings
    event_bus: AbstractEventBus
    state_manager: AbstractStateManager

    # ── Phase 2 Providers ────────────────────────────────────────
    providers: ProviderManager

    # ── Phase 3 Agent Core ───────────────────────────────────────
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    schema_validator: BaseSchemaValidator
    memory_manager: BaseMemoryManager
    prompt_builder: PromptBuilder
    agent_registry: Optional[AgentRegistry] = None
    session_agents: Optional[SessionAgentRegistry] = None
    workspace_manager: Optional[BaseWorkspaceManager] = None
    file_indexer: Optional[FileIndexer] = None
    orchestrator: Optional[Orchestrator] = None  # None until an agent is registered
    session_manager: Optional[AbstractSessionManager] = None
    observability: Optional["ObservabilityManager"] = None
    capability_probe: Optional[CapabilityProbeService] = None

    # ── Phase 4.2 Command System ─────────────────────────────────
    command_registry: Optional[CommandRegistry] = None
    command_parser: Optional[CommandParser] = None
    interaction_handler: Optional["BaseInteractionHandler"] = None
    file_tracker: Optional[FileChangeTracker] = None

    # ── Lifecycle State ──────────────────────────────────────────
    _started: bool = field(default=False, repr=False)
    _task_result_subscription_id: Optional[str] = field(default=None, repr=False)
    _autosave_task: Optional[asyncio.Task[None]] = field(default=None, repr=False)

    # ── Lifecycle Hooks (1.5.3) ──────────────────────────────────

    async def startup(self) -> None:
        """Initialize all components.  Called once before first use.

        Idempotent — calling multiple times is safe.
        """
        if self._started:
            logger.debug("AppContext already started, skipping.")
            return

        logger.info("AppContext starting up...")

        # Future phases will init more components here:
        # - Semantic memory connection
        # - Session database
        # Session creation is lazy and happens on first routed user request
        # (or explicit session actions from the UI overlay).
        if self.session_manager is not None:
            self.session_manager.clear_active()

        if self.session_manager is not None and self.settings.session_auto_save:
            if self._task_result_subscription_id is None:
                self._task_result_subscription_id = self.event_bus.subscribe(
                    "TaskResultEvent",
                    self._on_task_result_event,
                    priority=90,
                )

            session_defaults = self.data_registry.get_session_defaults()
            interval_seconds = float(
                session_defaults.get("auto_save_interval_seconds", 300.0)
            )
            if interval_seconds > 0 and self._autosave_task is None:
                self._autosave_task = asyncio.create_task(
                    self._periodic_session_autosave(interval_seconds),
                    name="session-autosave",
                )

        if self.file_indexer is not None:
            self.file_indexer.start(self.event_bus)

        runtime_identity: dict[str, str] = {
            "requested_model": self.settings.default_model,
            "provider": "unknown",
            "resolved_model": self.settings.default_model,
            "deployment_id": "unknown",
        }
        capability_sources: dict[str, str] = {
            "declared": "unknown",
            "observed": "unknown",
            "effective": "unknown",
        }
        try:
            runtime_identity = self.providers.get_runtime_identity(
                self.settings.default_model
            )
            capability_sources = self.providers.get_capability_source_summary()
        except Exception:
            logger.exception(
                "Failed to compute model-registry startup diagnostics (model=%s)",
                self.settings.default_model,
            )

        logger.info(
            "Model registry startup diagnostics",
            extra={
                "source": "bootstrap",
                "data": {
                    **runtime_identity,
                    "capability_sources": capability_sources,
                },
            },
        )
        if self.observability is not None:
            self.observability.record_migration_counter("resolver_usage")

        self._started = True
        if self.observability is not None:
            logger.info(
                "Observability session active: %s",
                self.observability.session_id,
            )
        logger.info(
            "AppContext ready (model=%s, tools=%d)",
            self.settings.default_model,
            len(self.tool_registry.get_all_names()),
        )

    async def shutdown(self) -> None:
        """Graceful shutdown — drains in-flight events and releases resources.

        Idempotent — calling multiple times is safe.
        """
        if not self._started:
            logger.debug("AppContext not started, skipping shutdown.")
            return

        logger.info("AppContext shutting down...")

        if self.session_manager is not None:
            self._save_active_session(reason="shutdown")

        if self._autosave_task is not None:
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass
            self._autosave_task = None

        if self._task_result_subscription_id is not None:
            self.event_bus.unsubscribe(self._task_result_subscription_id)
            self._task_result_subscription_id = None

        if self.file_indexer is not None:
            await self.file_indexer.shutdown()

        # Shut down orchestrator before draining the bus so in-flight
        # request callbacks can be cancelled promptly.
        if self.orchestrator:
            await self.orchestrator.shutdown()

        # Drain the Event Bus (waits for background tasks)
        await self.event_bus.drain()

        # Shut down interaction handler if present
        if self.interaction_handler is not None and hasattr(
            self.interaction_handler, "shutdown"
        ):
            shutdown = getattr(self.interaction_handler, "shutdown")
            if callable(shutdown):
                from typing import Awaitable, Callable, cast

                await cast(Callable[[], Awaitable[None]], shutdown)()

        # Future phases will shut down more components here:
        # - Persist session
        # - Close HTTP clients
        # - Close terminal processes

        if self.observability is not None:
            self.observability.shutdown()

        self._started = False
        logger.info("AppContext shutdown complete.")

    @property
    def is_running(self) -> bool:
        """Whether the app context has been started and not yet shut down."""
        return self._started

    async def _on_task_result_event(self, event: BaseEvent) -> None:
        """Auto-save the active session after task completion events."""
        if not isinstance(event, TaskResultEvent):
            return
        self._save_active_session(reason="task_result")

    async def _periodic_session_autosave(self, interval_seconds: float) -> None:
        """Periodic session auto-save loop."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

            if not self._started:
                continue
            self._save_active_session(reason="periodic")

    def _save_active_session(self, *, reason: str) -> bool:
        """Persist the currently active session if available."""
        if self.session_manager is None:
            return False

        active = self.session_manager.get_active()
        if active is None:
            return False

        try:
            # Note: total_cost is now managed incrementally by the Orchestrator per-task
            # to ensure session isolation and persistence across process restarts.
            self.session_manager.save(active)
        except Exception:
            logger.exception(
                "Failed to save active session (%s): %s",
                reason,
                active.session_id,
            )
            return False

        logger.debug(
            "Auto-saved active session (%s): %s",
            reason,
            active.session_id,
        )
        return True


# ══════════════════════════════════════════════════════════════════════
# Factory Function (1.5.1)
# ══════════════════════════════════════════════════════════════════════


def create_app(
    settings: Optional[AgentSettings] = None,
    root_folder: Optional[Union[str, Path]] = None,
) -> AppContext:
    """Bootstrap the application — the single wiring entry point.

    Args:
        settings: Pre-built settings (useful for testing).
                  If ``None``, settings are loaded from the standard
                  hierarchy (TOML → env → defaults).

    Returns:
        A fully wired ``AppContext`` ready for ``startup()``.
    """
    # 0. Data Registry (data-driven defaults)
    data_registry = DataRegistry()
    tool_defaults = data_registry.get_tool_defaults()
    context_budget = data_registry.get_context_budget()

    # 1. Configuration
    if settings is None:
        settings = AgentSettings()

    observability = configure_observability(settings, data_registry=data_registry)

    # 2. Event Bus
    event_bus = AsyncEventBus()

    # 3. State Manager (depends on Event Bus)
    state_manager = TaskStateManager(
        event_bus=event_bus,
        observability=observability,
    )

    # 4. Provider Manager (depends on Settings)
    providers = ProviderManager(
        settings,
        data_registry=data_registry,
        observability=observability,
    )
    capability_probe = CapabilityProbeService(
        data_registry=data_registry,
        observability=observability,
    )

    # 5. Tool System
    workspace_root = Path(root_folder) if root_folder else Path.cwd()
    strict_workspace = StrictWorkspaceManager(
        root_path=workspace_root,
        deny_patterns=settings.workspace_deny_patterns,
        allow_overrides=settings.workspace_allow_overrides,
    )
    workspace = SandboxWorkspaceManager(strict_workspace)
    file_indexer = FileIndexer(
        root_path=workspace.get_root(),
        max_files=int(tool_defaults.get("workspace", {}).get("index_max_files", 5000)),
    )

    # 5.5 File Tracker (Phase 4.4)
    file_tracker = FileChangeTracker(event_bus=event_bus)
    file_tracker.start_tracking(workspace.get_root())

    tool_registry = _build_tool_registry(workspace)
    output_formatter = ToolOutputFormatter(
        max_output_length=settings.tool_output_max_chars,
        data_registry=data_registry,
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=settings.auto_approve_tools,
        file_tracker=file_tracker,
        data_registry=data_registry,
        observability=observability,
    )

    # 6. Schema Validator
    schema_validator = SchemaValidator(
        registered_tools=tool_registry.get_all_names(),
        protocol_mode=settings.protocol_mode,
        data_registry=data_registry,
    )

    # 7. Memory Manager (token-aware)
    default_model = settings.default_model
    memory_manager = SummarizingMemoryManager(
        token_counter=providers.get_token_counter(default_model),
        token_budget=providers.get_token_budget(
            default_model,
            response_reserve=4096,
            compaction_threshold=float(
                context_budget.get("compaction_threshold", 0.80)
            ),
        ),
        model_name=default_model,
        summarizer_provider_factory=providers.get_provider,
        data_registry=data_registry,
    )

    # 8. Prompt Builder
    prompt_builder = PromptBuilder(
        tool_registry=tool_registry,
        data_registry=data_registry,
    )

    # 8.5 Session Manager (Phase 5.2.1)
    session_manager = FileSessionManager(default_model=settings.default_model)

    # 9. Command System (Phase 4.2)
    cmd_registry = _build_command_registry()
    cmd_context = CommandContext(
        settings=settings,
        event_bus=event_bus,
        state_manager=state_manager,
        memory_manager=memory_manager,
    )
    cmd_parser = CommandParser(registry=cmd_registry, context=cmd_context)

    # 10. Assemble context (orchestrator wired after agent setup)
    context = AppContext(
        data_registry=data_registry,
        settings=settings,
        event_bus=event_bus,
        state_manager=state_manager,
        observability=observability,
        providers=providers,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=memory_manager,
        prompt_builder=prompt_builder,
        agent_registry=None,
        session_agents=None,
        workspace_manager=workspace,
        file_indexer=file_indexer,
        orchestrator=None,
        session_manager=session_manager,
        capability_probe=capability_probe,
        command_registry=cmd_registry,
        command_parser=cmd_parser,
        interaction_handler=None,
        file_tracker=file_tracker,
    )

    # Wire up the back-reference so commands can access providers and orchestrator
    cmd_context.app_context = context

    # 11. Multi-agent setup
    from agent_cli.core.runtime.agents.agents.coder import CoderAgent
    from agent_cli.core.runtime.agents.agents.researcher import ResearcherAgent
    from agent_cli.core.runtime.agents.base import AgentConfig
    from agent_cli.core.runtime.agents.default import DefaultAgent

    def _parse_optional_max_iterations(raw: object) -> Optional[int]:
        if raw is None:
            return None
        value = str(raw).strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _resolve_agent_config(
        *,
        name: str,
        description: str,
        persona: str = "",
        default_tools: Optional[list[str]] = None,
    ) -> AgentConfig:
        override_raw = settings.agents.get(name, {})
        override = override_raw if isinstance(override_raw, dict) else {}

        model_name = str(override.get("model") or settings.default_model)
        baseline_tools = default_tools if default_tools is not None else all_tools
        tools = override.get("tools", baseline_tools)
        if not isinstance(tools, list):
            tools = baseline_tools

        return AgentConfig(
            name=name,
            description=str(override.get("description", description)),
            persona=str(override.get("persona", persona)).strip(),
            model=model_name,
            tools=[str(tool) for tool in tools],
            max_iterations_override=_parse_optional_max_iterations(
                override.get("max_iterations")
            ),
            show_thinking=bool(override.get("show_thinking", True)),
        )

    def _create_agent_instance(
        *,
        config: AgentConfig,
        agent_cls: type[DefaultAgent],
    ) -> DefaultAgent:
        model_name = config.model or settings.default_model
        config.model = model_name
        provider = providers.get_provider(model_name)
        agent_memory = _create_agent_memory(
            providers=providers,
            data_registry=data_registry,
            model_name=model_name,
        )
        return agent_cls(
            config=config,
            provider=provider,
            tool_executor=tool_executor,
            schema_validator=schema_validator,
            memory_manager=agent_memory,
            event_bus=event_bus,
            state_manager=state_manager,
            prompt_builder=prompt_builder,
            settings=settings,
            data_registry=data_registry,
        )

    all_tools = tool_registry.get_all_names()
    web_enabled_tools = [*all_tools, "web_search"]
    agent_registry = AgentRegistry()

    builtins = [
        (
            DefaultAgent,
            _resolve_agent_config(
                name="default",
                description="General-purpose assistant",
                default_tools=web_enabled_tools,
            ),
        ),
        (
            CoderAgent,
            _resolve_agent_config(
                name="coder",
                description="Implementation and refactoring specialist",
                default_tools=all_tools,
            ),
        ),
        (
            ResearcherAgent,
            _resolve_agent_config(
                name="researcher",
                description="Analysis and research specialist",
                default_tools=web_enabled_tools,
            ),
        ),
    ]

    for cls, cfg in builtins:
        agent_registry.register(_create_agent_instance(config=cfg, agent_cls=cls))

    # User-defined agents from [agents.*]
    builtin_names = {"default", "coder", "researcher"}
    for name, raw in settings.agents.items():
        if name in builtin_names:
            continue
        if agent_registry.has(name):
            logger.warning("Skipping user agent '%s': name already registered", name)
            continue
        if not isinstance(raw, dict):
            logger.warning("Skipping user agent '%s': config must be a mapping", name)
            continue

        model_name = str(raw.get("model") or settings.default_model)
        tools = raw.get("tools", all_tools)
        if not isinstance(tools, list):
            tools = all_tools

        user_config = AgentConfig(
            name=name,
            description=str(raw.get("description", f"User-defined agent '{name}'")),
            persona=str(raw.get("persona", "")).strip(),
            model=model_name,
            tools=[str(tool) for tool in tools],
            max_iterations_override=_parse_optional_max_iterations(
                raw.get("max_iterations")
            ),
            show_thinking=bool(raw.get("show_thinking", True)),
        )
        agent_registry.register(
            _create_agent_instance(config=user_config, agent_cls=DefaultAgent)
        )

    session_agents = SessionAgentRegistry()
    default_name = str(getattr(settings, "default_agent", "default"))
    resolved_default_name = (
        default_name if agent_registry.has(default_name) else "default"
    )
    if not agent_registry.has(resolved_default_name):
        raise RuntimeError("No default agent is registered.")

    if resolved_default_name != default_name:
        logger.warning(
            "Configured default_agent '%s' not found. Falling back to '%s'.",
            default_name,
            resolved_default_name,
        )

    default_agent = agent_registry.get(resolved_default_name)
    assert default_agent is not None

    session_agents.add(default_agent, activate=True)

    # Shared command context should target the active agent memory by default.
    context.memory_manager = default_agent.memory
    cmd_context.memory_manager = default_agent.memory

    context.agent_registry = agent_registry
    context.session_agents = session_agents

    # Freeze static registries after bootstrap population.
    tool_registry.freeze()
    agent_registry.freeze()
    cmd_registry.freeze()

    context.orchestrator = Orchestrator(
        event_bus=context.event_bus,
        state_manager=context.state_manager,
        default_agent=default_agent,
        command_parser=context.command_parser,
        session_manager=context.session_manager,
        agent_registry=agent_registry,
        session_agents=session_agents,
        capability_probe=context.capability_probe,
        observability=context.observability,
    )

    logger.info(
        "AppContext created with multi-agent registry (default=%s, total=%d).",
        resolved_default_name,
        len(agent_registry.get_all()),
    )
    logger.info("All registries frozen; bootstrap complete.")
    return context


def _create_agent_memory(
    *,
    providers: ProviderManager,
    data_registry: DataRegistry,
    model_name: str,
) -> SummarizingMemoryManager:
    """Create a per-agent memory manager with model-aware budget/token tools."""
    context_budget = data_registry.get_context_budget()
    return SummarizingMemoryManager(
        token_counter=providers.get_token_counter(model_name),
        token_budget=providers.get_token_budget(
            model_name,
            response_reserve=4096,
            compaction_threshold=float(
                context_budget.get("compaction_threshold", 0.80)
            ),
        ),
        model_name=model_name,
        summarizer_provider_factory=providers.get_provider,
        data_registry=data_registry,
    )




# ══════════════════════════════════════════════════════════════════════
# Tool Registry Builder
# ══════════════════════════════════════════════════════════════════════


def _build_tool_registry(workspace: BaseWorkspaceManager) -> ToolRegistry:
    """Create and populate the tool registry with all built-in tools."""
    from agent_cli.core.runtime.tools.ask_user_tool import AskUserTool
    from agent_cli.core.runtime.tools.file_tools import (
        InsertLinesTool,
        ListDirectoryTool,
        ReadFileTool,
        SearchFilesTool,
        StrReplaceTool,
        WriteFileTool,
    )
    from agent_cli.core.runtime.tools.shell_tool import RunCommandTool

    registry = ToolRegistry()

    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(StrReplaceTool(workspace))
    registry.register(InsertLinesTool(workspace))
    registry.register(RunCommandTool(workspace))
    registry.register(AskUserTool())

    logger.info(
        "Tool registry built with %d tools: %s",
        len(registry.get_all_names()),
        ", ".join(registry.get_all_names()),
    )
    return registry


# ══════════════════════════════════════════════════════════════════════
# Command Registry Builder (Phase 4.2)
# ══════════════════════════════════════════════════════════════════════


def _build_command_registry() -> CommandRegistry:
    """Create a CommandRegistry and register all built-in commands explicitly."""
    from agent_cli.core.ux.commands.handlers.agent import cmd_agent
    from agent_cli.core.ux.commands.handlers.core import (
        cmd_clear,
        cmd_config,
        cmd_context,
        cmd_cost,
        cmd_debug,
        cmd_effort,
        cmd_exit,
        cmd_help,
        cmd_model,
    )
    from agent_cli.core.ux.commands.handlers.sandbox import cmd_sandbox
    from agent_cli.core.ux.commands.handlers.session import cmd_generate_title, cmd_sessions

    registry = CommandRegistry()

    registry.register(
        CommandDef(
            name="help",
            description="Show all available commands",
            usage="/help [command]",
            shortcut="ctrl+?",
            category="System",
            handler=cmd_help,
        )
    )
    registry.register(
        CommandDef(
            name="exit",
            description="Exit the CLI",
            usage="/exit",
            shortcut="ctrl+q",
            category="System",
            handler=cmd_exit,
        )
    )
    registry.register(
        CommandDef(
            name="clear",
            description="Clear working memory (start fresh context)",
            usage="/clear",
            shortcut="ctrl+l",
            category="Memory",
            handler=cmd_clear,
        )
    )
    registry.register(
        CommandDef(
            name="context",
            description="Show context window usage",
            usage="/context",
            category="Memory",
            handler=cmd_context,
        )
    )
    registry.register(
        CommandDef(
            name="cost",
            description="Show session cost breakdown",
            usage="/cost",
            category="Memory",
            handler=cmd_cost,
        )
    )
    registry.register(
        CommandDef(
            name="model",
            description="Switch LLM model",
            usage="/model <name>",
            category="Model",
            handler=cmd_model,
        )
    )
    registry.register(
        CommandDef(
            name="effort",
            description="Get or set reasoning effort",
            usage="/effort [auto|minimal|low|medium|high|max]",
            category="Model",
            handler=cmd_effort,
        )
    )
    registry.register(
        CommandDef(
            name="debug",
            description="Toggle debug logging",
            usage="/debug [on|off]",
            category="Model",
            handler=cmd_debug,
        )
    )
    registry.register(
        CommandDef(
            name="config",
            description="View current settings (read-only)",
            usage="/config",
            category="Configuration",
            handler=cmd_config,
        )
    )
    registry.register(
        CommandDef(
            name="agent",
            description="Manage agents in the current session",
            usage="/agent [list|add|remove|enable|disable|default] [name]",
            category="Agent",
            handler=cmd_agent,
        )
    )
    registry.register(
        CommandDef(
            name="sandbox",
            description="Control workspace sandbox mode",
            usage="/sandbox <on|off|ls>",
            category="Workspace",
            handler=cmd_sandbox,
        )
    )
    registry.register(
        CommandDef(
            name="sessions",
            description="Open session manager overlay",
            usage="/sessions",
            category="Session",
            handler=cmd_sessions,
        )
    )
    registry.register(
        CommandDef(
            name="generate_title",
            description="Generate a new title for the active session",
            usage="/generate_title",
            category="Session",
            handler=cmd_generate_title,
        )
    )

    logger.info(
        "Command registry built with %d commands: %s",
        len(registry.all()),
        ", ".join(c.name for c in registry.all()),
    )
    return registry

