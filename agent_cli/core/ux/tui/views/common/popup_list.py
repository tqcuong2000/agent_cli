"""
BasePopupListView — A reusable fuzzy-filtered popup list widget.

Used by:
  - CommandPopup: triggered by `/` in input bar (command suggestions)
  - FileDiscoveryPopup: triggered by `@` in input bar (file search)

Subclasses only need to implement:
  - render_item(): how each row looks
  - on_item_selected(): what happens when user picks an item
  - get_trigger_char(): which character activates the popup
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget


@dataclass
class PopupItem:
    """A single item in the popup list."""

    label: str  # Primary display text
    description: str = ""  # Secondary text (right-aligned or below)
    icon: str = ""  # Leading icon character
    hint: str = ""  # Right-most hint (e.g., keyboard shortcut)
    value: str = ""  # The value inserted into the input on selection
    data: object = None  # Arbitrary data attached to this item


class BasePopupListView(Widget):
    """
    Abstract base for a floating popup list that appears above the input bar.

    Handles:
    - Show/hide lifecycle
    - Fuzzy filtering based on user input
    - Keyboard navigation (↑/↓/Enter/Tab/Esc)
    - Scroll if items exceed max_visible

    Subclasses implement:
    - get_trigger_char() → which character activates the popup
    - get_all_items() → the full unfiltered item list
    - render_item() → how to display each row
    - on_item_selected() → callback when user picks an item
    - filter_items() → optional custom filtering (default: fuzzy on label)
    """

    DEFAULT_CSS = ""

    selected_index: reactive[int] = reactive(0, always_update=True)
    filter_text: reactive[str] = reactive("", always_update=True)

    class ItemSelected(Message):
        """Emitted when the user selects an item from the popup."""

        def __init__(self, item: PopupItem, trigger_char: str):
            self.item = item
            self.trigger_char = trigger_char
            super().__init__()

    class Dismissed(Message):
        """Emitted when the popup is dismissed without selection."""

        pass

    def __init__(self, max_visible: int = 10, **kwargs):
        popup_id = kwargs.pop("id", "popup_list")
        super().__init__(id=popup_id, **kwargs)
        self._max_visible = max_visible
        self._filtered_items: List[PopupItem] = []
        self._all_items: List[PopupItem] = []

    # ── Abstract methods (subclasses must implement) ─────────

    def get_trigger_char(self) -> str:
        """Return the character that activates this popup (e.g., '/' or '@')."""
        raise NotImplementedError

    def get_all_items(self) -> List[PopupItem]:
        """Return the full unfiltered list of items."""
        raise NotImplementedError

    def render_item(self, item: PopupItem, is_selected: bool) -> str:
        """
        Return a Rich-formatted string for a single row.

        Args:
            item: The popup item to render.
            is_selected: Whether this row is currently highlighted.
        """
        raise NotImplementedError

    def on_item_selected(self, item: PopupItem) -> str:
        """
        Called when the user selects an item.
        Returns the text to insert into the input bar.
        """
        raise NotImplementedError

    # ── Filtering ────────────────────────────────────────────

    def filter_items(self, query: str, items: List[PopupItem]) -> List[PopupItem]:
        """
        Default fuzzy filter: match if query chars appear in order in label.
        Subclasses can override for custom filtering.
        """
        if not query:
            return items

        query_lower = query.lower()
        results = []

        for item in items:
            label_lower = item.label.lower()
            # Prefix match gets priority
            if label_lower.startswith(query_lower):
                results.insert(0, item)
            elif query_lower in label_lower:
                results.append(item)
            else:
                # Fuzzy: check if all chars appear in order
                qi = 0
                for char in label_lower:
                    if qi < len(query_lower) and char == query_lower[qi]:
                        qi += 1
                if qi == len(query_lower):
                    results.append(item)

        return results

    # ── Show / Hide ──────────────────────────────────────────

    def show_popup(self, initial_filter: str = "") -> None:
        """Show the popup and optionally set an initial filter."""
        self._all_items = self.get_all_items()
        self.filter_text = initial_filter
        self.selected_index = 0
        self._apply_filter()
        self._position_above_footer()
        self.add_class("visible")

    def _position_above_footer(self) -> None:
        """Dynamically set margin-bottom to sit above the footer."""
        try:
            from agent_cli.core.ux.tui.views.footer.footer import FooterContainer

            footer = self.app.query_one(FooterContainer)
            # Footer outer height = content + borders
            footer_height = footer.outer_size.height
            self.styles.margin = (0, 0, footer_height, 1)
        except Exception:
            # Fallback to reasonable default
            self.styles.margin = (0, 0, 4, 1)

    def hide_popup(self) -> None:
        """Hide the popup."""
        self.remove_class("visible")
        self.filter_text = ""
        self._filtered_items = []

    @property
    def is_visible(self) -> bool:
        """Whether the popup is currently shown."""
        return self.has_class("visible")

    # ── Keyboard Navigation ──────────────────────────────────

    def handles_key(self, event: events.Key) -> bool:
        """
        Handle keyboard events while the popup is visible.
        Returns True if the event was consumed.

        Called from UserInputComponent._on_key() when popup is visible.
        """
        if not self.is_visible:
            return False

        key = event.key.lower()

        if key == "up":
            self._move_selection(-1)
            return True

        elif key == "down":
            self._move_selection(1)
            return True

        elif key in ("tab", "enter"):
            if not self._filtered_items:
                # No matches — dismiss popup and let the key fall through
                self.hide_popup()
                self.post_message(self.Dismissed())
                return False
            self._select_current()
            return True

        elif key == "escape":
            self.hide_popup()
            self.post_message(self.Dismissed())
            return True

        return False

    def update_filter(self, query: str) -> None:
        """Update the filter text (called as user types)."""
        self.filter_text = query
        self.selected_index = 0
        self._apply_filter()

    # ── Internal ─────────────────────────────────────────────

    def _apply_filter(self) -> None:
        """Apply the current filter and re-render."""
        self._filtered_items = self.filter_items(self.filter_text, self._all_items)
        self._refresh_render()

    def _move_selection(self, delta: int) -> None:
        """Move the selection up or down."""
        if not self._filtered_items:
            return
        new_index = self.selected_index + delta
        self.selected_index = max(0, min(new_index, len(self._filtered_items) - 1))
        self._refresh_render()

    def _select_current(self) -> None:
        """Select the currently highlighted item."""
        if not self._filtered_items:
            return
        item = self._filtered_items[self.selected_index]
        # insert_text = self.on_item_selected(item)
        self.post_message(self.ItemSelected(item, self.get_trigger_char()))
        self.hide_popup()

    def _refresh_render(self) -> None:
        """Re-render all rows. Simple approach: update the widget content."""
        self.refresh()

    def render(self) -> str:
        """Render the popup list as a Rich-formatted string."""
        if not self._filtered_items:
            return "[dim italic]  No matches[/]"

        lines = []
        visible = self._filtered_items[: self._max_visible]

        for i, item in enumerate(visible):
            is_selected = i == self.selected_index
            line = self.render_item(item, is_selected)
            lines.append(line)

        if len(self._filtered_items) > self._max_visible:
            remaining = len(self._filtered_items) - self._max_visible
            lines.append(f"[dim]  ... {remaining} more[/]")

        return "\n".join(lines)
