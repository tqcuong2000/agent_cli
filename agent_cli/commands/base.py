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

from agent_cli.core.registry_base import RegistryLifecycleMixin

if TYPE_CHECKING:
    from textual.app import App

    from agent_cli.agent.memory import BaseMemoryManager
    from agent_cli.core.config import AgentSettings
    from agent_cli.core.events.event_bus import AbstractEventBus
    from agent_cli.core.state.state_manager import AbstractStateManager
    from agent_cli.core.bootstrap import AppContext

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
        self._registry: Dict[str, CommandDef] = {}
        self._registry_name = "commands"

    # ── Mutation ─────────────────────────────────────────────────

    def register(self, cmd: CommandDef, *, override: bool = False) -> None:
        """Register a command definition."""
        self._assert_mutable()
        key = cmd.name.lower()
        if key in self._registry and not override:
            raise ValueError(f"Command '/{cmd.name}' is already registered.")
        self._registry[key] = cmd
        logger.debug("Registered command: /%s", cmd.name)

    def _freeze_summary(self) -> str:
        return f"{len(self._registry)} commands"

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

