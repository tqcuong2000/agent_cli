"""
CommandParser — parses and executes '/' commands.

Sits between the TUI input and the Event Bus.  If the raw input
starts with ``/``, the parser intercepts it, looks up the command
in the ``CommandRegistry``, and calls the handler.
"""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING, List

from agent_cli.commands.base import (
    CommandContext,
    CommandDef,
    CommandRegistry,
    CommandResult,
)

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger(__name__)


class CommandParser:
    """Parses ``/command args`` strings and dispatches to handlers."""

    def __init__(
        self,
        registry: CommandRegistry,
        context: CommandContext,
    ) -> None:
        self._registry = registry
        self._context = context

    # ── Public API ───────────────────────────────────────────────

    @property
    def app(self) -> "App | None":
        """Current Textual app attached to the command context."""
        return self._context.app

    def set_app(self, app: "App | None") -> None:
        """Attach the active Textual app so handlers can update UI widgets."""
        self._context.app = app

    @staticmethod
    def is_command(text: str) -> bool:
        """Check whether *text* is a slash command."""
        return text.strip().startswith("/")

    async def execute(self, raw: str) -> CommandResult:
        """Parse and execute a command.

        Returns ``CommandResult`` with success/failure and a
        user-facing message.
        """
        raw = raw.strip()
        without_slash = raw[1:]  # drop leading '/'

        # Tokenise (shlex honours quotes; fallback on parse error)
        try:
            parts = shlex.split(without_slash)
        except ValueError:
            parts = without_slash.split()

        if not parts:
            return CommandResult(
                success=False,
                message="Empty command. Type /help for a list.",
            )

        cmd_name = parts[0].lower()
        args = parts[1:]

        # ── Lookup ───────────────────────────────────────────────
        cmd_def = self._registry.get(cmd_name)

        if cmd_def is None:
            suggestions = self._registry.get_suggestions(cmd_name)
            if suggestions:
                names = ", ".join(f"/{s.name}" for s in suggestions[:3])
                return CommandResult(
                    success=False,
                    message=(f"Unknown command: /{cmd_name}. Did you mean: {names}?"),
                )
            return CommandResult(
                success=False,
                message=(f"Unknown command: /{cmd_name}. Type /help for a list."),
            )

        # ── Execute ──────────────────────────────────────────────
        try:
            return await cmd_def.handler(args, self._context)
        except Exception as e:
            logger.error("Command /%s raised: %s", cmd_name, e, exc_info=True)
            return CommandResult(
                success=False,
                message=f"Command error: {e}",
            )

    # ── Autocomplete helpers ─────────────────────────────────────

    def get_suggestions(self, partial: str) -> List[CommandDef]:
        """Delegate to registry for autocomplete suggestions."""
        return self._registry.get_suggestions(partial)

    def get_all_commands(self) -> List[CommandDef]:
        """Return all registered commands."""
        return self._registry.all()
