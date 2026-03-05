"""Command system foundation: registry, models, and execution context."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
)

from agent_cli.core.infra.registry.registry_base import RegistryLifecycleMixin

if TYPE_CHECKING:
    from textual.app import App

    from agent_cli.core.runtime.agents.memory import BaseMemoryManager
    from agent_cli.core.infra.config.config import AgentSettings
    from agent_cli.core.infra.events.event_bus import AbstractEventBus
    from agent_cli.core.runtime.orchestrator.state_manager import AbstractStateManager
    from agent_cli.core.infra.registry.bootstrap import AppContext

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════════════


@dataclass
class CommandResult:
    """Result returned by every command handler."""

    success: bool
    message: str
    data: Any = None


# Type alias for handler signature
CommandHandler = Callable[..., Coroutine[Any, Any, CommandResult]]


@dataclass
class CommandDef:
    """Definition of a registered command."""

    name: str
    description: str
    usage: str = ""
    handler: CommandHandler = None  # type: ignore[assignment]
    shortcut: Optional[str] = None
    category: str = "General"


@dataclass
class CommandContext:
    """Dependencies injected into every command handler.

    Provides access to system components without global imports.
    Future phases will add ``session_manager``, ``workspace``,
    ``change_tracker``, etc.
    """

    settings: AgentSettings
    event_bus: AbstractEventBus
    state_manager: AbstractStateManager
    memory_manager: BaseMemoryManager
    app: Optional[App] = None
    app_context: Optional["AppContext"] = None


# ══════════════════════════════════════════════════════════════════════
# Command Registry
# ══════════════════════════════════════════════════════════════════════


class CommandRegistry(RegistryLifecycleMixin):
    """Central catalog of all registered slash commands.

    Instantiated once in bootstrap and shared via ``AppContext``.
    """

    def __init__(self) -> None:
        super().__init__(registry_name="commands")
        self._registry: Dict[str, CommandDef] = {}

    # ── Mutation ─────────────────────────────────────────────────

    def register(self, cmd: CommandDef, *, override: bool = False) -> None:
        """Register a command definition."""
        self._assert_mutable()
        name = str(cmd.name).strip()
        if not name:
            raise ValueError("Command must have a non-empty name.")
        if cmd.handler is None or not callable(cmd.handler):
            raise ValueError(f"Command '/{name}' must have a callable handler.")

        key = name.lower()
        if key in self._registry and not override:
            raise ValueError(f"Command '/{name}' is already registered.")
        self._registry[key] = cmd
        logger.debug("Registered command: /%s", name)

    def _freeze_summary(self) -> str:
        return f"{len(self._registry)} commands"

    def validate(self) -> None:
        if not self._registry:
            raise RuntimeError(
                "Command registry must contain at least one command before freeze."
            )

    # ── Lookup ───────────────────────────────────────────────────

    def get(self, name: str) -> Optional[CommandDef]:
        """Look up a command by exact name (case-insensitive)."""
        return self._registry.get(name.lower())

    def all(self) -> List[CommandDef]:
        """Return all registered commands sorted by category then name."""
        return sorted(
            self._registry.values(),
            key=lambda c: (c.category, c.name),
        )

    def get_suggestions(self, partial: str) -> List[CommandDef]:
        """Prefix + substring match for autocomplete.

        Prefix matches are sorted first, then substring matches.
        """
        partial = partial.lower()
        matches: List[CommandDef] = []

        for cmd in self._registry.values():
            if cmd.name.startswith(partial) or partial in cmd.name:
                matches.append(cmd)

        # Prefix matches first, then alphabetical
        matches.sort(
            key=lambda c: (not c.name.startswith(partial), c.name)
        )
        return matches

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return str(name).strip().lower() in self._registry
