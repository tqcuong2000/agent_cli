"""
CommandPopup — '/' triggered command suggestion popup.

Shows available slash commands with fuzzy filtering.
Triggered when the user types '/' in the input bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from agent_cli.ux.tui.views.common.popup_list import BasePopupListView, PopupItem

if TYPE_CHECKING:
    from agent_cli.commands.base import CommandRegistry


@dataclass
class CommandInfo:
    """Definition of a slash command for the popup."""

    name: str
    description: str
    shortcut: str = ""
    category: str = "General"


# ── Static command registry (will be replaced by dynamic registry later) ──
_COMMANDS: List[CommandInfo] = [
    # Agent
    CommandInfo("agent", "Manage agents in this session", "", "Agent"),
    # Model
    CommandInfo("model", "Switch LLM model", "", "Model"),
    CommandInfo("debug", "Toggle debug logging", "", "Model"),
    # Configuration
    CommandInfo("config", "View or modify settings", "", "Configuration"),
    # Session
    CommandInfo("sessions", "Open session manager overlay", "", "Session"),
    # Workspace
    CommandInfo("sandbox", "Toggle sandbox mode", "", "Workspace"),
    # Memory
    CommandInfo("clear", "Clear working memory", "ctrl+l", "Memory"),
    CommandInfo("context", "Show context window usage", "", "Memory"),
    CommandInfo("cost", "Show session cost", "", "Memory"),
    # UI
    CommandInfo("theme", "Switch TUI theme", "", "UI"),
    CommandInfo("changes", "Show changed files", "", "UI"),
    # System
    CommandInfo("help", "Show all commands", "ctrl+?", "System"),
    CommandInfo("exit", "Exit the CLI", "ctrl+q", "System"),
]


class CommandPopup(BasePopupListView):
    """
    Popup showing available '/' commands with fuzzy filtering.
    Appears when the user types '/' in the input bar.
    """

    DEFAULT_CSS = """
    CommandPopup {
        width: 56;
        border: solid $primary-darken-2;
    }
    """

    def __init__(
        self,
        commands: List[CommandInfo] | None = None,
        registry: Optional[CommandRegistry] = None,
        **kwargs,
    ):
        kwargs.setdefault("id", "command_popup")
        super().__init__(max_visible=10, **kwargs)
        self._commands = commands or _COMMANDS
        self._registry = registry

    def get_trigger_char(self) -> str:
        return "/"

    def get_all_items(self) -> List[PopupItem]:
        """Convert command entries to PopupItems.

        If a live ``CommandRegistry`` is available, use it;
        otherwise fall back to the static ``_COMMANDS`` list.
        """
        if self._registry is not None:
            return [
                PopupItem(
                    label=cmd.name,
                    description=cmd.description,
                    icon="/",
                    hint=cmd.shortcut or "",
                    value=f"/{cmd.name} ",
                    data=cmd,
                )
                for cmd in self._registry.all()
            ]

        return [
            PopupItem(
                label=cmd.name,
                description=cmd.description,
                icon="/",
                hint=cmd.shortcut,
                value=f"/{cmd.name} ",
                data=cmd,
            )
            for cmd in self._commands
        ]

    def render_item(self, item: PopupItem, is_selected: bool) -> str:
        """Render a command row: /name  description  shortcut."""
        prefix = "▸ " if is_selected else "  "
        bg = "[on #1a3a5c]" if is_selected else ""
        bg_end = "[/]" if is_selected else ""

        desc = f"[dim]{item.description}[/]"

        # Pad name to fixed width for alignment
        name_padded = f"/{item.label}".ljust(14)
        name_styled = f"[bold cyan]{name_padded}[/]"

        if item.hint:
            hint = f"[dim italic]{item.hint}[/]"
            return f"{bg}{prefix}{name_styled} {desc}  {hint}{bg_end}"
        else:
            return f"{bg}{prefix}{name_styled} {desc}{bg_end}"

    def on_item_selected(self, item: PopupItem) -> str:
        """Return the command text to insert into the input bar."""
        return item.value

    def set_commands(self, commands: List[CommandInfo]) -> None:
        """Update the command list (for dynamic registration)."""
        self._commands = commands
