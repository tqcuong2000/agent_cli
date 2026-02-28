"""
Command System Foundation — decorator, registry, and context.

Provides the ``@command`` decorator for registering slash commands,
a ``CommandRegistry`` for lookup/suggestion, and the ``CommandContext``
that every handler receives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
)

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


class CommandRegistry:
    """Central catalog of all registered slash commands.

    Instantiated once (in bootstrap) and shared via ``AppContext``.
    The module-level ``_DEFAULT_REGISTRY`` collects decorator
    registrations and is absorbed into this instance at startup.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, CommandDef] = {}

    # ── Mutation ─────────────────────────────────────────────────

    def register(self, cmd: CommandDef) -> None:
        """Register a command definition."""
        self._registry[cmd.name.lower()] = cmd
        logger.debug("Registered command: /%s", cmd.name)

    def absorb(self, other: CommandRegistry) -> None:
        """Merge all commands from *other* into this registry."""
        for cmd in other._registry.values():
            self.register(cmd)

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


# ══════════════════════════════════════════════════════════════════════
# Module-level default registry (populated by @command decorator)
# ══════════════════════════════════════════════════════════════════════

_DEFAULT_REGISTRY = CommandRegistry()


# ══════════════════════════════════════════════════════════════════════
# @command Decorator
# ══════════════════════════════════════════════════════════════════════


def command(
    name: str,
    description: str,
    usage: str = "",
    shortcut: Optional[str] = None,
    category: str = "General",
) -> Callable:
    """Decorator to register a slash command.

    Usage::

        @command(name="help", description="Show all commands",
                 usage="/help [command]", shortcut="ctrl+?",
                 category="System")
        async def cmd_help(args: List[str], ctx: CommandContext) -> CommandResult:
            ...

    The handler is registered into the module-level
    ``_DEFAULT_REGISTRY`` at import time.  At bootstrap,
    ``_DEFAULT_REGISTRY`` is absorbed into the real
    ``CommandRegistry`` that lives in ``AppContext``.
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        cmd_def = CommandDef(
            name=name,
            description=description,
            usage=usage or f"/{name}",
            handler=func,
            shortcut=shortcut,
            category=category,
        )
        _DEFAULT_REGISTRY.register(cmd_def)

        @wraps(func)
        async def wrapper(*a: Any, **kw: Any) -> CommandResult:
            return await func(*a, **kw)

        return wrapper

    return decorator
