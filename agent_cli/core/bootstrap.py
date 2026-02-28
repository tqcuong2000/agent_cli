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

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from agent_cli.core.config import AgentSettings, load_providers
from agent_cli.core.events.event_bus import AbstractEventBus, AsyncEventBus
from agent_cli.core.state.state_manager import AbstractStateManager, TaskStateManager
from agent_cli.providers.manager import ProviderManager

# Phase 3 imports
from agent_cli.agent.memory import BaseMemoryManager, WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.schema import BaseSchemaValidator, SchemaValidator
from agent_cli.core.orchestrator import Orchestrator
from agent_cli.tools.executor import ToolExecutor
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry
from agent_cli.tools.workspace import WorkspaceContext

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
    orchestrator: Optional[Orchestrator] = None  # None until an agent is registered

    # ── Lifecycle State ──────────────────────────────────────────
    _started: bool = field(default=False, repr=False)

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

        self._started = True
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

        # Drain the Event Bus (waits for background tasks)
        await self.event_bus.drain()

        # Shut down orchestrator
        if self.orchestrator:
            await self.orchestrator.shutdown()

        # Future phases will shut down more components here:
        # - Persist session
        # - Close HTTP clients
        # - Close terminal processes

        self._started = False
        logger.info("AppContext shutdown complete.")

    @property
    def is_running(self) -> bool:
        """Whether the app context has been started and not yet shut down."""
        return self._started


# ══════════════════════════════════════════════════════════════════════
# Factory Function (1.5.1)
# ══════════════════════════════════════════════════════════════════════


def create_app(
    settings: Optional[AgentSettings] = None,
) -> AppContext:
    """Bootstrap the application — the single wiring entry point.

    Args:
        settings: Pre-built settings (useful for testing).
                  If ``None``, settings are loaded from the standard
                  hierarchy (TOML → env → defaults).

    Returns:
        A fully wired ``AppContext`` ready for ``startup()``.
    """
    # 1. Configuration
    if settings is None:
        settings = AgentSettings()

    # 2. Event Bus
    event_bus = AsyncEventBus()

    # 3. State Manager (depends on Event Bus)
    state_manager = TaskStateManager(event_bus=event_bus)

    # 4. Provider Manager (depends on Settings)
    providers = ProviderManager(settings)

    # 5. Tool System
    workspace = WorkspaceContext(root_path=Path.cwd())
    tool_registry = _build_tool_registry(workspace)
    output_formatter = ToolOutputFormatter(
        max_output_length=settings.tool_output_max_chars,
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=settings.auto_approve_tools,
    )

    # 6. Schema Validator
    schema_validator = SchemaValidator(
        registered_tools=tool_registry.get_all_names(),
    )

    # 7. Memory Manager
    memory_manager = WorkingMemoryManager()

    # 8. Prompt Builder
    prompt_builder = PromptBuilder(tool_registry=tool_registry)

    # 9. Assemble context
    #    Orchestrator is None until an agent is registered via
    #    ``register_default_agent()``.
    context = AppContext(
        settings=settings,
        event_bus=event_bus,
        state_manager=state_manager,
        providers=providers,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=memory_manager,
        prompt_builder=prompt_builder,
        orchestrator=None,
    )

    logger.info("AppContext created (startup pending).")
    return context


def register_default_agent(
    context: AppContext, agent: BaseAgent
) -> None:
    """Wire a default agent into the Orchestrator.

    Call this after ``create_app()`` to activate the Orchestrator.
    The agent is typically constructed using components from the
    ``AppContext``.
    """
    context.orchestrator = Orchestrator(
        event_bus=context.event_bus,
        state_manager=context.state_manager,
        default_agent=agent,
    )
    logger.info(
        "Default agent '%s' registered with Orchestrator.", agent.name
    )


# ══════════════════════════════════════════════════════════════════════
# Tool Registry Builder
# ══════════════════════════════════════════════════════════════════════


def _build_tool_registry(workspace: WorkspaceContext) -> ToolRegistry:
    """Create and populate the tool registry with all built-in tools."""
    from agent_cli.tools.file_tools import (
        ListDirectoryTool,
        ReadFileTool,
        SearchFilesTool,
        WriteFileTool,
    )
    from agent_cli.tools.shell_tool import RunCommandTool

    registry = ToolRegistry()

    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(RunCommandTool(workspace))

    logger.info(
        "Tool registry built with %d tools: %s",
        len(registry.get_all_names()),
        ", ".join(registry.get_all_names()),
    )
    return registry
