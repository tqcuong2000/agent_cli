"""
CommandPopup - '/' triggered command suggestion popup.

Shows available slash commands with fuzzy filtering.
Triggered when the user types '/' in the input bar.
"""

from __future__ import annotations

from agent_cli.core.ux.commands.base import CommandRegistry
from agent_cli.core.ux.tui.views.common.popup_list import BasePopupListView, PopupItem


class CommandPopup(BasePopupListView):
    """
    Popup showing available '/' commands with fuzzy filtering.
    Appears when the user types '/' in the input bar.
    """

    DEFAULT_CSS = ""

    def __init__(self, registry: CommandRegistry, **kwargs):
        kwargs.setdefault("id", "command_popup")
        super().__init__(max_visible=10, **kwargs)
        self._registry = registry

    def get_trigger_char(self) -> str:
        return "/"

    def get_all_items(self) -> list[PopupItem]:
        """Convert command entries to PopupItems."""
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
        return f"{bg}{prefix}{name_styled} {desc}{bg_end}"

    def on_item_selected(self, item: PopupItem) -> str:
        """Return the command text to insert into the input bar."""
        return item.value
